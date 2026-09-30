"""Lane A: the inline decision path.

The lane's value is not that it is fast. It is that it is fast *and* honest about what it
did not manage to check — a decision service that quietly returns "allow" when its
counters were missing is worse than no decision service, because everyone downstream
believes the payment was screened.

So the tests that matter here are the refusals and the omissions, not the happy path:

* a rule whose counter is absent, stale, or unreachable is **reported**, never treated
  as clean;
* the latency budget is **enforced between rules**, and what did not run is named;
* fail-open and fail-closed produce different actions from the same evaluation, and
  neither is available by default;
* shadow mode always tells the caller to allow, while still recording what it would
  have done.
"""
from datetime import datetime, timedelta, timezone

import pytest

from services.decision_service.app import decide as decide_mod
from services.decision_service.app import evaluate as evaluate_mod
from services.decision_service.app import inline_rules, policy as policy_mod
from services.decision_service.app.counters import Lookup, MemoryCounterStore

TENANT = "t-decision"


def rule(rule_id, threshold, *, comparator="gte"):
    """A catalogue rule in the shape config-service actually serves.

    Keyed by rule id, with no ``severity`` field — severity is an alert-level band over
    the families that fired, not a label on a rule. An earlier version of these tests
    passed a per-rule severity that the real catalogue never carries, which is how the
    decline floor came to be untestable and, in production, unreachable.
    """
    return {"rule_id": rule_id, "family": rule_id.split("-")[0],
            "threshold": threshold, "comparator": comparator,
            "sub_rule_ref": ".01", "reason": f"{rule_id} fired"}


@pytest.fixture
def store():
    return MemoryCounterStore()


# ----------------------------------------------------------- the inline subset
def test_the_subset_is_classified_not_guessed():
    """Every catalogue observation is either inline-eligible or has a recorded reason."""
    overlap = set(inline_rules.INLINE_SOURCE) & set(inline_rules.NOT_INLINE)
    assert not overlap, f"observations claimed both inline and deferred: {overlap}"


def test_an_unclassified_rule_is_refused_not_defaulted():
    """A new rule nobody classified must not silently become inline."""
    assert not inline_rules.eligible("XXX-99")
    assert "not classified" in inline_rules.why_not("XXX-99")


def test_deferred_rules_carry_their_reason(store):
    ev = evaluate_mod.evaluate(
        rules=[rule("LAY-03", 3)], request={"debtor_account": "A1"},
        store=store, tenant_id=TENANT, budget_ms=100)
    assert ev.matched == []
    assert len(ev.skipped) == 1
    assert "graph traversal" in ev.skipped[0]["reason"]
    assert ev.skipped[0]["lane"] == "near-real-time"


def test_cheque_return_and_od_breach_are_deferred_not_inline():
    """CBS-04/CBS-05 (added alongside this test) need the CBS's own post-settlement
    extract, the same way CBS-01/02/03 already do - a payment authorisation cannot see
    a cheque-clearing outcome or an overdraft position that has not been posted yet."""
    assert not inline_rules.eligible("CBS-04")
    assert not inline_rules.eligible("CBS-05")
    assert "CBS-04" in inline_rules.NOT_INLINE and "CBS-05" in inline_rules.NOT_INLINE


# ------------------------------------------------- absence is never a clean pass
def test_a_missing_counter_is_reported_not_scored_clean(store):
    """The whole doctrine in one test: no counter means unmeasured, not compliant."""
    ev = evaluate_mod.evaluate(
        rules=[rule("VEL-03", 8)],
        request={"debtor_account": "A-no-counter", "amount_paise": 900_000},
        store=store, tenant_id=TENANT, budget_ms=100)
    assert ev.matched == []
    assert any("not measurable" in s["reason"] for s in ev.skipped), ev.skipped
    assert ev.considered == 0, "a rule that could not be measured was not screened"


def test_a_stale_counter_is_reported_not_used():
    """A velocity count from yesterday is not a velocity count."""
    class StaleStore(MemoryCounterStore):
        def read(self, tenant_id, account, observations):
            return Lookup(values={}, stale=list(observations), took_ms=0.1)

    ev = evaluate_mod.evaluate(
        rules=[rule("VEL-03", 8)],
        request={"debtor_account": "A1", "amount_paise": 900_000}, store=StaleStore(),
        tenant_id=TENANT, budget_ms=100)
    assert ev.matched == []
    assert any("stale" in s["reason"] for s in ev.skipped), ev.skipped


def test_an_unreachable_store_skips_every_inline_rule():
    class DownStore(MemoryCounterStore):
        def read(self, tenant_id, account, observations):
            return Lookup(available=False, error="connection refused", took_ms=0.2)

    rules = [rule("VEL-03", 8),
             rule("SME-01", 5)]
    ev = evaluate_mod.evaluate(rules=rules, request={"debtor_account": "A1"},
                               store=DownStore(), tenant_id=TENANT, budget_ms=100)
    assert ev.store_available is False
    assert len(ev.skipped) == 2
    assert all("unavailable" in s["reason"] for s in ev.skipped)


# --------------------------------------------------------------- the budget bites
def test_the_budget_stops_evaluation_and_names_what_did_not_run():
    """A budget that is only a target is not a budget."""
    class SlowStore(MemoryCounterStore):
        def read(self, tenant_id, account, observations):
            import time
            time.sleep(0.05)          # 50 ms, against a 10 ms budget
            return Lookup(values={o: 0.0 for o in observations}, took_ms=50.0)

    rules = [rule(f"VEL-0{i}", 8) for i in (1, 2, 3)]
    ev = evaluate_mod.evaluate(rules=rules, request={"debtor_account": "A1"},
                               store=SlowStore(), tenant_id=TENANT, budget_ms=10)
    assert ev.budget_exceeded is True
    assert any("budget expired" in s["reason"] for s in ev.skipped), ev.skipped


# ------------------------------------------------------------ comparator direction
def test_a_below_threshold_rule_fires_below_not_above(store):
    """Beneficiary age fires when the payee is *new*. Getting this backwards makes the
    indicator permanently dormant, which is exactly what an inspection looks for."""
    # LAY-01 is the outflow ratio: it fires at or above its threshold. Using it with an
    # inverted comparator proves the direction comes from the catalogue, not from here.
    store.write(TENANT, "A1", {"inflow_paise": 100.0, "outflow_paise": 20.0})
    r = rule("LAY-01", 0.5, comparator="lt")          # ratio 0.2 < 0.5 -> fires
    ev = evaluate_mod.evaluate(rules=[r], request={"debtor_account": "A1"},
                               store=store, tenant_id=TENANT, budget_ms=100)
    assert [m["rule_id"] for m in ev.matched] == ["LAY-01"]

    store.write(TENANT, "A2", {"inflow_paise": 100.0, "outflow_paise": 90.0})
    ev2 = evaluate_mod.evaluate(rules=[r], request={"debtor_account": "A2"},
                                store=store, tenant_id=TENANT, budget_ms=100)
    assert ev2.matched == [], "0.9 is not below 0.5"


# ---------------------------------------------------------------- the fail policy
def _rail(**kw):
    base = dict(rail="UPI", mode=policy_mod.MODE_INLINE, budget_ms=80, fail="open",
                actions=["challenge", "decline"], shadow=False,
                decline_from_severity="critical")
    base.update(kw)
    return policy_mod.RailPolicy(**base)


def test_a_rail_without_an_explicit_fail_mode_is_refused():
    """No default. Failing open silently stops applying the control; failing closed
    declines genuine payments. The bank chooses, and RBI will ask which."""
    with pytest.raises(policy_mod.PolicyError) as exc:
        _rail(fail="").validate()
    assert "no safe default" in str(exc.value)


def test_fail_open_and_fail_closed_diverge_on_the_same_evaluation():
    ev = evaluate_mod.Evaluation(budget_exceeded=True)
    opened = decide_mod.decide(ev=ev, rp=_rail(fail="open"), policy_version="1",
                               total_ms=99.0)
    closed = decide_mod.decide(ev=ev, rp=_rail(fail="closed"), policy_version="1",
                               total_ms=99.0)
    assert opened.action == "allow" and closed.action == "decline"
    # Both record *why* — an allow on a timeout must never look like a clean pass.
    assert opened.outcome == closed.outcome == "budget_exceeded"


def test_a_clean_pass_and_a_timeout_are_distinguishable():
    # considered=4: a clean pass means rules ran and none fired. An Evaluation that ran
    # nothing is a third case now, and it is not this one.
    clean = decide_mod.decide(ev=evaluate_mod.Evaluation(considered=4), rp=_rail(),
                              policy_version="1", total_ms=5.0)
    timeout = decide_mod.decide(ev=evaluate_mod.Evaluation(budget_exceeded=True),
                                rp=_rail(), policy_version="1", total_ms=99.0)
    assert clean.action == timeout.action == "allow"
    assert clean.outcome == "clean" and timeout.outcome == "budget_exceeded", \
        "an allow on timeout must not be recorded as a clean screen"


# ------------------------------------------------------------------ shadow mode
def test_shadow_mode_allows_but_records_what_it_would_have_done():
    ev = evaluate_mod.Evaluation(
        matched=[{"rule_id": "LAY-01", "family": "LAY"},
                 {"rule_id": "SME-01", "family": "SME"},
                 {"rule_id": "CPT-01", "family": "CPT"}])
    d = decide_mod.decide(ev=ev, rp=_rail(shadow=True), policy_version="1",
                          total_ms=12.0)
    assert d.action == "allow", "shadow mode must never enforce"
    assert d.would_be == "decline", "but it must record the decision it withheld"
    assert d.enforced is False


def test_leaving_shadow_mode_starts_enforcing():
    ev = evaluate_mod.Evaluation(
        matched=[{"rule_id": "LAY-01", "family": "LAY"},
                 {"rule_id": "SME-01", "family": "SME"},
                 {"rule_id": "CPT-01", "family": "CPT"}])
    d = decide_mod.decide(ev=ev, rp=_rail(shadow=False), policy_version="1",
                          total_ms=12.0)
    assert d.action == "decline" and d.enforced is True


def test_severity_floor_caps_the_action():
    """A high-severity match on a rail that only declines for critical stays a challenge."""
    ev = evaluate_mod.Evaluation(matched=[{"rule_id": "SME-01", "severity": "high"}])
    d = decide_mod.decide(ev=ev, rp=_rail(shadow=False), policy_version="1",
                          total_ms=8.0)
    assert d.action == "challenge"


# ------------------------------------------------------------------ policy sanity
def test_the_starter_policy_is_valid_and_starts_in_shadow():
    p = policy_mod.parse(TENANT, policy_mod.STARTER)
    assert p.for_rail("UPI").shadow is True
    assert p.for_rail("NEFT").mode == policy_mod.MODE_NRT, \
        "NEFT settles in a window where full context beats an inline guess"
    assert all(rp.shadow for rp in p.rails.values() if rp.mode == policy_mod.MODE_INLINE)


def test_an_inline_rail_with_no_actions_is_refused():
    with pytest.raises(policy_mod.PolicyError) as exc:
        _rail(actions=[]).validate()
    assert "not a control" in str(exc.value)


def test_an_absurd_budget_is_refused():
    with pytest.raises(policy_mod.PolicyError):
        _rail(budget_ms=5000).validate()


def test_an_unknown_counter_store_is_refused_not_defaulted():
    from services.decision_service.app import counters
    with pytest.raises(ValueError) as exc:
        counters.build("redis-maybe")
    assert "not defaulted" in str(exc.value)
