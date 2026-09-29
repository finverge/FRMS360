"""Evaluate the inline rule subset against one payment, inside a latency budget.

Three properties matter more than speed here, and the first two are the ones that make
this defensible rather than merely fast.

**A rule that did not run is recorded, never assumed clean.** If a counter is missing,
stale, or the budget expires mid-evaluation, the rule appears in ``skipped`` with its
reason. The platform says this everywhere else — an absent measurement is not a zero —
and it matters most here, where the alternative is telling a bank a payment was screened
when it was not.

**The budget is checked between rules, not hoped for.** Evaluation stops the moment the
clock says it must, and what did not run is reported. A budget that is only a target is
not a budget.

**Comparator direction comes from the catalogue, not from here.** Most indicators fire at
or above a threshold; the age-based ones fire *below* it, because a two-hour-old payee is
more suspicious than a twenty-hour-old one. Re-deriving that in this module would be a
second implementation of rule semantics, and the two lanes would eventually disagree.
"""
from __future__ import annotations

import time
from datetime import datetime, timezone
from dataclasses import dataclass, field

from cp_common import observations

from . import context, inline_rules
from .counters import CounterStore, Lookup


@dataclass
class Evaluation:
    matched: list[dict] = field(default_factory=list)
    skipped: list[dict] = field(default_factory=list)
    lookup_ms: float = 0.0
    evaluate_ms: float = 0.0
    budget_exceeded: bool = False
    store_available: bool = True
    #: How many inline-eligible rules were actually run. Zero is not a clean pass - it
    #: means nothing was screened, and the two must never share an outcome.
    considered: int = 0


def _fires(observed: float, threshold: float, comparator: str) -> bool:
    """One place, so both lanes read the comparator the same way."""
    c = (comparator or "gte").strip().lower()
    if c in ("lt", "below", "less_than"):
        return observed < threshold
    if c in ("lte",):
        return observed <= threshold
    if c in ("gt",):
        return observed > threshold
    if c in ("eq",):
        return observed == threshold
    return observed >= threshold


def _observation(rule) -> str:
    return rule.get("observation") if isinstance(rule, dict) else getattr(rule, "observation", "")


def _get(rule, key, default=None):
    return rule.get(key, default) if isinstance(rule, dict) else getattr(rule, key, default)


def evaluate(*, rules, request: dict, store: CounterStore, tenant_id: str,
             budget_ms: int, now: datetime | None = None) -> Evaluation:
    """Score one payment against the inline-eligible rules.

    The observation comes from ``cp_common.observations.observe`` — the same function the
    near-real-time engine calls. Counters are read once, an ``AccountContext`` is rebuilt
    from them, and ``observe`` turns that context plus this payment into a value per rule
    id. That indirection is the point: if this lane computed its own idea of "velocity"
    the two lanes could disagree about the same account and there would be no way to say
    which was right.

    ``request`` carries the payment plus whatever the channel already knows. A signal the
    channel supplies is used in preference to anything derived, because at authorisation
    time the channel is the better-informed party.
    """
    now = now or datetime.now(timezone.utc)
    started = time.perf_counter()
    ev = Evaluation()

    inline, deferred = inline_rules.split(rules)
    for r in deferred:
        ev.skipped.append({
            "rule_id": inline_rules.rule_id_of(r),
            "reason": inline_rules.why_not(inline_rules.rule_id_of(r)),
            "lane": "near-real-time",
        })

    if not inline:
        # ``considered`` stays 0, which decide() turns into an explicit not-screened
        # outcome rather than an allow. An empty catalogue used to read as a clean pass:
        # every payment allowed, every log line saying the controls had run.
        ev.evaluate_ms = (time.perf_counter() - started) * 1000
        return ev

    # One round trip. Every counter the context needs is fetched together rather than per
    # rule, because the cost here is the round trip and not the columns.
    account = request.get("debtor_account", "")
    look: Lookup = store.read(tenant_id, account, list(context.COUNTER_FIELDS))
    ev.lookup_ms = look.took_ms
    ev.store_available = look.available

    if not look.available:
        for r in inline:
            ev.skipped.append({"rule_id": inline_rules.rule_id_of(r),
                               "reason": f"counter store unavailable: {look.error}",
                               "lane": "inline"})
        ev.evaluate_ms = (time.perf_counter() - started) * 1000 - ev.lookup_ms
        return ev

    ctx = context.rebuild(look.values, now=now)
    txn = {
        "debtor_account": account,
        "creditor_account": request.get("creditor_account", ""),
        "amount_paise": int(request.get("amount_paise", 0) or 0),
        "rail": str(request.get("rail", "")).upper(),
        "device_id": request.get("device_id", "") or "",
        "ts": now,
    }
    # The device cluster size is a counter, so the cluster map has exactly one entry —
    # the device on this payment. observe() reads it by device id.
    clusters = ({txn["device_id"]: int(look.values["device_cluster_size"])}
                if txn["device_id"] and "device_cluster_size" in look.values else {})

    # cycles / hour_profile / screening are all None: none of them is reconstructable from
    # account counters, and observe() leaves the rules that need them unmeasured rather
    # than scoring them zero. That is why those rule ids are in NOT_INLINE.
    observed = observations.observe(txn, ctx, clusters, now, subject="debtor")

    # Rules whose inputs are set-backed must not be read off a context that has no sets.
    # CPT-01 and VEL-02 are 'lte' rules on beneficiary age: an unknown pair reads as age
    # zero, which would fire them on every high-value payment. Dropped explicitly here so
    # the suppression is visible rather than depending on NOT_INLINE alone.
    for rule_id in context.NEEDS_SET_STORE:
        observed.pop(rule_id, None)
    # Same reasoning for CHN-01: derived from empty device/counterparty sets it would
    # count every payment as new-device-and-new-payee. Only the channel's own figure is
    # trusted, and if it does not send one the rule is reported unmeasured.
    observed.pop("CHN-01", None)
    if "risk_signals" in request:
        observed["CHN-01"] = float(request["risk_signals"])

    eval_started = time.perf_counter()
    for i, r in enumerate(inline):
        # Budget check between rules. Whatever is left is reported, not silently dropped.
        elapsed_ms = (time.perf_counter() - started) * 1000
        if elapsed_ms >= budget_ms:
            ev.budget_exceeded = True
            for rest in inline[i:]:
                ev.skipped.append({"rule_id": inline_rules.rule_id_of(rest),
                                   "reason": "latency budget expired before evaluation",
                                   "lane": "inline"})
            break

        rule_id = inline_rules.rule_id_of(r)
        if rule_id not in observed:
            # Not measurable for *this* payment - a dormancy rule on an account with no
            # last-seen, a UPI-only rule on an RTGS payment. Reported, never scored zero.
            #
            # Staleness is named separately from absence. The store already refuses to
            # hand back a counter past its freshness horizon, so stale data is never
            # scored; but "the feed has stopped" and "this account has no history" are
            # different operational problems and an operator needs to tell them apart.
            reason = (context.unmeasurable(rule_id)
                      or "not measurable for this payment from account counters")
            if look.stale:
                reason += (f" ({len(look.stale)} counter(s) stale and therefore not used: "
                           f"{', '.join(sorted(look.stale)[:4])})")
            ev.skipped.append({"rule_id": rule_id, "reason": reason, "lane": "inline"})
            continue

        # Counted only now: a rule that could not be measured was not screened, and
        # ``considered`` is what decide() uses to tell a real pass from an empty one.
        ev.considered += 1
        value = observed[rule_id]
        threshold = float(_get(r, "threshold", 0) or 0)
        if _fires(value, threshold, _get(r, "comparator", "gte")):
            ev.matched.append({
                "rule_id": rule_id,
                "family": _get(r, "family", ""),
                "sub_rule_ref": _get(r, "sub_rule_ref", ""),
                "observed": value,
                "threshold": threshold,
                "observed_unit": _get(r, "observed_unit", ""),
                "reason": _get(r, "reason", ""),
                # No per-rule severity. The catalogue does not carry one, and severity is
                # a property of the whole match set - it is on the decision, not here.
                # Leaving an always-empty field in the contract invites an integrator to
                # write code against it.
            })

    ev.evaluate_ms = (time.perf_counter() - eval_started) * 1000
    return ev
