"""Re-scoring a historical window: simulation, and replay.

Two different questions get asked of a detection estate, and conflating them is how banks
end up with an alert queue nobody trusts.

**"What would this threshold have caught last quarter?"** is a *simulation*. It must create
nothing at all - no alerts, no cases, no notifications. It is the question asked before a
band is retuned, and the only safe answer is a comparison against what actually fired.

**"We fixed a broken rule; score that window properly."** is a *replay*. It writes real
alerts, and it is dangerous in two specific ways this module refuses to be:

* **It must never duplicate.** An alert already exists for (txn, rule); writing a second
  one inflates every count on every dashboard and the reconciliation invariants fail.
* **It must never disturb human work.** If an analyst has already dispositioned an alert
  or an investigator has taken a case through the lifecycle, a replay that reopened or
  overwrote that would destroy the audit trail the whole platform exists to keep. Replay
  therefore only ever *adds* alerts that were missing, and it never attaches them to a
  case that is past triage.

**Point-in-time correctness.** Observations are rebuilt **per day**, with each day's
context cut at that day's own start, so a March transaction is scored against March's
normality rather than against everything learned since. That is the lookahead bias which
makes naive backfills look far more accurate than the live system ever was.

Day granularity is not a nicety. An earlier version built one context at the end of the
whole window, and on any window longer than a day the 24-hour rules - LAY-01, LAY-02,
VEL-03 - could not fire at all, because every transaction was measured against the final
day's counterparty counts. The simulator returned confident-looking numbers that were
systematically wrong for exactly the bands an operator most wants to tune. Rebuilding per
day is what the live pipeline does: each batch is scored against its own recent history.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from sqlalchemy import text
from sqlalchemy.orm import Session

from ..models import FactAlert
from . import cbs_features, features, graph_features, reference

#: A window wider than this is refused. Re-scoring five years in one transaction is a
#: denial of service a bank performs on itself.
MAX_WINDOW_DAYS = 92
#: Transactions per run. Beyond this the caller should narrow the window.
MAX_ROWS = 200_000

#: States in which a case is still just triage. A replay may attach a newly-found alert
#: to one of these; anything further along has had human judgement applied to it.
UNWORKED_CASE_STATES = ("under_review",)


@dataclass
class BackfillReport:
    mode: str
    tenant_id: str
    window_from: datetime
    window_to: datetime
    transactions: int = 0
    would_fire: dict[str, int] = field(default_factory=dict)
    actual: dict[str, int] = field(default_factory=dict)
    added: dict[str, int] = field(default_factory=dict)
    removed: dict[str, int] = field(default_factory=dict)
    written: int = 0
    skipped_existing: int = 0
    skipped_worked_case: int = 0
    unmeasurable: dict[str, str] = field(default_factory=dict)
    samples: list[dict] = field(default_factory=list)
    note: str = ""

    def as_dict(self) -> dict:
        return {
            "mode": self.mode, "tenant_id": self.tenant_id,
            "window_from": self.window_from, "window_to": self.window_to,
            "transactions": self.transactions,
            "would_fire": self.would_fire, "actual": self.actual,
            "added": self.added, "removed": self.removed,
            "written": self.written, "skipped_existing": self.skipped_existing,
            "skipped_worked_case": self.skipped_worked_case,
            "unmeasurable": self.unmeasurable, "samples": self.samples[:25],
            "note": self.note,
        }


class BackfillRefused(Exception):
    pass


def _load_window(db: Session, tenant_id: str, start: datetime, end: datetime) -> list[dict]:
    rows = db.execute(text("""
        SELECT txn_id, ts, rail, amount_paise, debtor_account, creditor_account,
               branch, region, product, customer_segment, channel, device_id,
               ip_addr, status, source
          FROM analytics.fact_transaction
         WHERE tenant_id = :t AND ts >= :s AND ts < :e
         ORDER BY ts
         LIMIT :lim
    """), {"t": tenant_id, "s": start, "e": end, "lim": MAX_ROWS + 1}).mappings().all()
    if len(rows) > MAX_ROWS:
        raise BackfillRefused(
            f"The window holds more than {MAX_ROWS} transactions. Narrow it: re-scoring "
            "this much in one pass would hold a transaction open for hours.")
    return [dict(r) for r in rows]



def _score_slice(db: Session, slice_txns: list[dict], *, rep: BackfillReport, mode: str,
                 tenant_id: str, catalogue: dict, policy: dict, ctxs: dict,
                 clusters: dict, cycles: dict, profiles: dict, screening: dict,
                 loan_ctx: dict, as_of: datetime, existing: set,
                 weights: dict, typology: dict, severity_of) -> None:
    """Score one day's transactions against that day's context."""
    for t in slice_txns:
        ctx = ctxs.get(t["debtor_account"], features.AccountContext())
        obs = features.observe(t, ctx, clusters, as_of, cycles=cycles,
                               screening=screening,
                               hour_profile=profiles.get(t["debtor_account"]))
        loan = loan_ctx.get(t["debtor_account"])
        if loan is not None:
            for rid, v in cbs_features.observe_loan(loan).items():
                obs[rid] = max(obs.get(rid, 0.0), v)
        cred = ctxs.get(t["creditor_account"])
        if cred is not None:
            for rid, v in features.observe(t, cred, clusters, as_of,
                                           subject="creditor", cycles=cycles).items():
                obs[rid] = max(obs.get(rid, 0.0), v)

        fired = []
        for rule_id, observed in obs.items():
            rule = catalogue.get(rule_id)
            if not rule or rule.get("threshold") is None:
                continue
            threshold = float(rule["threshold"])
            fires = (observed <= threshold if rule.get("comparator") == "lte"
                     else observed >= threshold)
            if fires:
                fired.append((rule_id, rule, observed, threshold))

        if not fired:
            continue
        for rule_id, _rule, _o, _th in fired:
            rep.would_fire[rule_id] = rep.would_fire.get(rule_id, 0) + 1

        if mode == "simulate":
            if len(rep.samples) < 25:
                rep.samples.append({
                    "txn_id": t["txn_id"], "ts": t["ts"],
                    "amount_paise": t["amount_paise"],
                    "rules": [{"rule_id": r[0], "observed": round(float(r[2]), 4),
                               "threshold": r[3]} for r in fired],
                })
            continue

        # ---- replay: write only what is genuinely missing -----------------------
        score = sum(weights.get(r[1].get("family", ""), 100) for r in fired)
        severity = severity_of(score, policy)
        version = next((r[1].get("version") for r in fired if r[1].get("version")),
                       "1.0.0")
        for rule_id, rule, observed, threshold in fired:
            if (t["txn_id"], rule_id) in existing:
                rep.skipped_existing += 1
                continue
            db.add(FactAlert(
                alert_id="A" + uuid.uuid4().hex[:16], tenant_id=tenant_id,
                txn_id=t["txn_id"], ts=t["ts"],
                rule_family=rule.get("family", ""), rule_id=rule_id,
                typology=typology.get(rule.get("family", ""), "Unclassified"),
                score=float(score), severity=severity, disposition="pending",
                # Deliberately unattached. Joining a replayed alert onto a case an
                # investigator has already worked would change the evidence behind a
                # decision that has been taken - and possibly one already reported.
                case_id=None,
                config_version=rule.get("version") or version,
                sub_rule_ref=rule.get("sub_rule_ref") or "",
                matched_reason=rule.get("reason") or "Threshold exceeded",
                observed_value=float(observed), threshold_value=threshold,
                observed_unit=rule.get("observed_unit") or "",
                # So a replayed alert is never mistaken for one the live pipeline raised.
                source="replay"))
            rep.written += 1
            # Remember it, so a later day in the same run cannot write it twice.
            existing.add((t["txn_id"], rule_id))



def run(db: Session, *, tenant_id: str, window_from: datetime, window_to: datetime,
        catalogue: dict, policy: dict, mode: str = "simulate",
        actor: str = "system:backfill") -> BackfillReport:
    """Score a historical window. ``mode`` is 'simulate' (default) or 'replay'."""
    if mode not in ("simulate", "replay"):
        raise BackfillRefused(f"Unknown mode '{mode}'. Use 'simulate' or 'replay'.")
    if window_to <= window_from:
        raise BackfillRefused("The window ends before it starts.")
    if (window_to - window_from) > timedelta(days=MAX_WINDOW_DAYS):
        raise BackfillRefused(
            f"Window is wider than {MAX_WINDOW_DAYS} days. Run it in slices - a single "
            "pass over years of history is a denial of service on your own database.")

    rep = BackfillReport(mode=mode, tenant_id=tenant_id, window_from=window_from,
                         window_to=window_to)
    txns = _load_window(db, tenant_id, window_from, window_to)
    rep.transactions = len(txns)
    if not txns:
        rep.note = "No transactions in this window."
        return rep

    # Reference data and the group register do not move within the window, so they are
    # loaded once. Everything else is rebuilt per day - see below.
    screening = reference.load_screening_set(db, tenant_id)

    # What actually fired in this window, for the comparison.
    for r in db.execute(text(
        "SELECT rule_id, COUNT(*) AS n FROM analytics.fact_alert "
        "WHERE tenant_id = :t AND ts >= :s AND ts < :e GROUP BY rule_id"),
            {"t": tenant_id, "s": window_from, "e": window_to}).mappings():
        rep.actual[r["rule_id"]] = int(r["n"])

    existing: set[tuple[str, str]] = set()
    if mode == "replay":
        for r in db.execute(text(
            "SELECT txn_id, rule_id FROM analytics.fact_alert "
            "WHERE tenant_id = :t AND ts >= :s AND ts < :e"),
                {"t": tenant_id, "s": window_from, "e": window_to}).mappings():
            existing.add((r["txn_id"], r["rule_id"]))

    from .engine import FAMILY_WEIGHT, TYPOLOGY, _severity

    # ---- score a day at a time -----------------------------------------------------
    # Every observation is an aggregate over a recent window - 24 hours for the velocity
    # and counterparty rules, 30 days for the baseline. Building one context at the end
    # of the whole period and scoring every transaction against it does not merely
    # approximate: a March transaction gets measured against the counterparty counts of
    # the final day, so on any window longer than a day the 24-hour rules cannot fire at
    # all. That made the simulator quietly useless for exactly the bands an operator
    # most wants to tune, while still returning confident-looking numbers.
    #
    # So the window is sliced by day and the context rebuilt for each, which is what the
    # live pipeline does - every batch is scored against its own recent history.
    by_day: dict[datetime, list[dict]] = {}
    for t in txns:
        key = t["ts"].replace(hour=0, minute=0, second=0, microsecond=0)
        by_day.setdefault(key, []).append(t)

    for day_start in sorted(by_day):
        slice_txns = by_day[day_start]
        day_end = day_start + timedelta(days=1)
        accounts = sorted({t["debtor_account"] for t in slice_txns} |
                          {t["creditor_account"] for t in slice_txns})
        ctxs = features.load_context(db, tenant_id, accounts, day_end,
                                     baseline_before=day_start)
        clusters = features.device_clusters(
            db, tenant_id,
            sorted({t.get("device_id") or "" for t in slice_txns} - {""}), day_end)
        cycles = graph_features.circular_flows(db, tenant_id, accounts, day_end)
        profiles = graph_features.hour_profiles(db, tenant_id, accounts, day_end,
                                                before=day_start)
        group_accts = cbs_features.group_accounts(db, tenant_id)
        loan_ctx = cbs_features.load_loan_context(db, tenant_id, accounts, day_end,
                                                  group_accounts=group_accts)
        _score_slice(db, slice_txns, rep=rep, mode=mode, tenant_id=tenant_id,
                     catalogue=catalogue, policy=policy, ctxs=ctxs, clusters=clusters,
                     cycles=cycles, profiles=profiles, screening=screening,
                     loan_ctx=loan_ctx, as_of=day_end, existing=existing,
                     weights=FAMILY_WEIGHT, typology=TYPOLOGY, severity_of=_severity)

    if mode == "replay":
        db.commit()

    rules = set(rep.would_fire) | set(rep.actual)
    for rid in rules:
        delta = rep.would_fire.get(rid, 0) - rep.actual.get(rid, 0)
        if delta > 0:
            rep.added[rid] = delta
        elif delta < 0:
            rep.removed[rid] = -delta

    rep.unmeasurable = {r: why for r, why in features.NEEDS_EXTERNAL_DATA.items()
                        if r in catalogue}
    for rule_id, kind in features.NEEDS_REFERENCE_DATA.items():
        if rule_id in catalogue:
            st = screening["availability"].get(kind)
            if st is not None and not st.loaded:
                rep.unmeasurable[rule_id] = st.why_unavailable

    rep.note = (
        "Simulation only - nothing was written. Counts are what this configuration "
        "would have produced over the window, scored against the data as it stood at "
        "the window's end."
        if mode == "simulate" else
        f"Replay wrote {rep.written} alert(s) marked source='replay'. Existing alerts "
        f"were left untouched and no alert was attached to a case.")
    return rep
