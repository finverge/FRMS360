"""What each rail can actually execute, as distinct from what it can be configured with.

Prompted by a review question — "shouldn't Lane A just be approve or decline for real-time
payments?" It should, and the shipped STARTER policy did not: it put ``challenge`` on UPI
and made IMPS challenge-only, so IMPS could never decline anything at all.

The failure this prevents is the one that matters most in this codebase. Returning an
action the switch cannot carry means the decision log records an enforced control, the
switch ignores an answer it does not recognise, and the payment settles. Every report says
the control fired. Nothing says it did not.

Capability is therefore a property of the payment scheme, checked at configuration time,
not a preference a tenant can set.
"""
import pytest

# ------------------------------------------------- what a rail can actually execute
# Added after a review question: "shouldn't Lane A just be approve or decline for
# real-time payments?" It should, for UPI and IMPS, and the shipped STARTER policy had
# `challenge` on both. The switch cannot execute a challenge on either rail, so the
# decision log would have recorded an enforced control while the payment settled.
def test_upi_cannot_be_configured_to_challenge():
    from services.decision_service.app.policy import PolicyError, RailPolicy
    rp = RailPolicy(rail="UPI", mode="inline", budget_ms=80, fail="open",
                    actions=["challenge"])
    with pytest.raises(PolicyError) as exc:
        rp.validate()
    assert "not an answer this rail can execute" in str(exc.value)
    assert "already authenticated" in str(exc.value)


def test_a_real_time_rail_cannot_hold_a_payment():
    from services.decision_service.app.policy import PolicyError, RailPolicy
    for rail in ("UPI", "IMPS", "CARD"):
        rp = RailPolicy(rail=rail, mode="inline", budget_ms=100, fail="open",
                        actions=["hold"])
        with pytest.raises(PolicyError) as exc:
            rp.validate()
        assert "execute" in str(exc.value)


def test_card_may_challenge_because_3ds_is_a_real_step_up():
    from services.decision_service.app.policy import RailPolicy
    RailPolicy(rail="CARD", mode="inline", budget_ms=100, fail="open",
               actions=["challenge", "decline"]).validate()


def test_neft_may_hold_because_it_settles_in_a_window():
    from services.decision_service.app.policy import RailPolicy
    RailPolicy(rail="NEFT", mode="inline", budget_ms=500, fail="open",
               actions=["hold", "decline"]).validate()


def test_an_undeclared_rail_is_refused_rather_than_assumed_permissive():
    """A new scheme whose capabilities nobody has established must not inherit the most
    powerful action set by default."""
    from services.decision_service.app.policy import PolicyError, RailPolicy
    with pytest.raises(PolicyError) as exc:
        RailPolicy(rail="SOMENEWRAIL", mode="inline", budget_ms=100, fail="open",
                   actions=["decline"]).validate()
    assert "no action capabilities are declared" in str(exc.value)


def test_the_shipped_starter_policy_only_promises_what_each_rail_can_do():
    """The regression. STARTER is what every new tenant gets, so an unexecutable action
    here would ship to every bank."""
    from services.decision_service.app.policy import RAIL_ACTIONS, STARTER
    for rail, spec in STARTER["rails"].items():
        for action in spec.get("actions", []):
            assert action in RAIL_ACTIONS[rail], f"STARTER {rail} promises {action}"


def test_upi_and_imps_are_binary_in_the_shipped_policy():
    from services.decision_service.app.policy import STARTER
    for rail in ("UPI", "IMPS"):
        assert STARTER["rails"][rail]["actions"] == ["decline"]


def test_a_match_below_the_decline_floor_allows_but_records_that_it_matched():
    """With a binary rail there is no intermediate action, so a sub-critical match
    allows. It must not be recorded as a clean pass - Lane B still needs to see it."""
    from services.decision_service.app import decide as decide_mod
    from services.decision_service.app.evaluate import Evaluation
    from services.decision_service.app.policy import RailPolicy

    rp = RailPolicy(rail="UPI", mode="inline", budget_ms=80, fail="open",
                    actions=["decline"], decline_from_severity="critical", shadow=False)
    ev = Evaluation(matched=[{"rule_id": "VEL-01", "severity": "high"}], skipped=[],
                    store_available=True, budget_exceeded=False, considered=6,
                    lookup_ms=1.0, evaluate_ms=1.0)
    d = decide_mod.decide(ev=ev, rp=rp, policy_version="1.0.0", total_ms=5.0)
    assert d.action == "allow"
    assert d.outcome == "matched", "a match that allowed must not read as clean"


# ------------------------------------------- an empty catalogue is not a clean pass
def test_evaluating_no_rules_is_not_reported_as_clean():
    """The most dangerous configuration failure this lane has.

    ``set_catalogue`` was never called by anything, so the inline catalogue was empty in
    every running deployment. Every payment evaluated zero rules, matched nothing, and was
    logged as ``allow / clean`` — indistinguishable in the record from a payment that
    passed every check. The lane reported perfect health while screening nothing.
    """
    from services.decision_service.app import decide as decide_mod
    from services.decision_service.app.evaluate import Evaluation
    from services.decision_service.app.policy import RailPolicy

    ev = Evaluation(matched=[], skipped=[], store_available=True,
                    budget_exceeded=False, considered=0)
    rp = RailPolicy(rail="UPI", mode="inline", budget_ms=80, fail="open",
                    actions=["decline"], shadow=False)
    d = decide_mod.decide(ev=ev, rp=rp, policy_version="1.0.0", total_ms=2.0)
    assert d.outcome == "not_screened"
    assert d.outcome != "clean"


def test_a_rail_that_fails_closed_declines_when_nothing_was_screened():
    """A bank choosing fail-closed has said it would rather stop a payment than let one
    through unchecked. An empty catalogue is exactly that situation."""
    from services.decision_service.app import decide as decide_mod
    from services.decision_service.app.evaluate import Evaluation
    from services.decision_service.app.policy import RailPolicy

    ev = Evaluation(considered=0, store_available=True, budget_exceeded=False)
    rp = RailPolicy(rail="UPI", mode="inline", budget_ms=80, fail="closed",
                    actions=["decline"], shadow=False)
    d = decide_mod.decide(ev=ev, rp=rp, policy_version="1.0.0", total_ms=2.0)
    assert d.action == "decline"
    assert d.outcome == "not_screened"


def test_a_run_that_evaluated_rules_and_matched_nothing_is_still_clean():
    """The fix must not turn every quiet payment into an alarm."""
    from services.decision_service.app import decide as decide_mod
    from services.decision_service.app.evaluate import Evaluation
    from services.decision_service.app.policy import RailPolicy

    ev = Evaluation(considered=7, store_available=True, budget_exceeded=False)
    rp = RailPolicy(rail="UPI", mode="inline", budget_ms=80, fail="open",
                    actions=["decline"], shadow=False)
    d = decide_mod.decide(ev=ev, rp=rp, policy_version="1.0.0", total_ms=2.0)
    assert d.action == "allow"
    assert d.outcome == "clean"


def test_not_screened_is_a_declared_outcome():
    from services.decision_service.app.models import OUTCOMES
    assert "not_screened" in OUTCOMES


def test_a_match_proves_rules_ran_even_if_the_counter_says_otherwise():
    """Belt and braces on a fail-closed rail. A bookkeeping slip in this service must not
    become a declined payment for a customer whose transaction was in fact screened."""
    from services.decision_service.app import decide as decide_mod
    from services.decision_service.app.evaluate import Evaluation
    from services.decision_service.app.policy import RailPolicy

    ev = Evaluation(matched=[{"rule_id": "VEL-01", "severity": "critical"}],
                    considered=0, store_available=True, budget_exceeded=False)
    rp = RailPolicy(rail="UPI", mode="inline", budget_ms=80, fail="closed",
                    actions=["decline"], decline_from_severity="critical", shadow=False)
    d = decide_mod.decide(ev=ev, rp=rp, policy_version="1.0.0", total_ms=2.0)
    assert d.outcome == "matched"


# ------------------------------------------------- the catalogue, and the vocabulary
def test_inline_classification_is_keyed_by_rule_id_not_an_invented_vocabulary():
    """The bug that made the whole lane inert.

    ``inline_rules`` was keyed by observation names (``value_vs_baseline``) that no
    catalogue and no config-service response ever contained. Nothing matched, every split
    put every rule in the deferred pile, and the lane screened nothing while reporting
    healthy. Rule ids are the vocabulary everything else speaks.
    """
    from services.decision_service.app import inline_rules as ir
    import re
    for key in list(ir.INLINE_SOURCE) + list(ir.NOT_INLINE):
        assert re.fullmatch(r"[A-Z]{3,4}-\d{2}", key), f"{key} is not a rule id"


def test_every_catalogue_rule_is_classified_exactly_once():
    from services.decision_service.app import inline_rules as ir
    overlap = set(ir.INLINE_SOURCE) & set(ir.NOT_INLINE)
    assert not overlap, f"claimed both inline and deferred: {overlap}"


def test_the_two_lanes_share_one_severity_definition():
    """Lane A must not band a score differently from the detection engine."""
    from cp_common import scoring
    from services.analytics_service.app.detection import engine
    assert engine.FAMILY_WEIGHT is scoring.FAMILY_WEIGHT
    assert engine._severity(400, {}) == scoring.severity_for(400, {})


def test_severity_comes_from_families_not_from_a_rule_field():
    """A lone velocity hit is not critical; velocity with layering and structuring is."""
    from services.decision_service.app.decide import severity_of
    _, one = severity_of([{"rule_id": "VEL-01", "family": "VEL"}])
    score, many = severity_of([{"family": "LAY"}, {"family": "SME"},
                               {"family": "CPT"}, {"family": "VEL"}])
    assert one == "low"
    assert many == "critical", score


def test_a_rule_carrying_a_severity_field_does_not_override_the_score():
    """The catalogue has no severity field. If one appears it must not quietly become
    the decision — that is how the old per-rule reading came to be unreachable."""
    from services.decision_service.app.decide import severity_of
    _, sev = severity_of([{"rule_id": "CHN-01", "family": "CHN",
                           "severity": "critical"}])
    assert sev == "low"


def test_set_backed_rules_are_never_scored_from_a_context_without_sets():
    """CPT-01 and VEL-02 are 'lte' rules on beneficiary age. Read off a context with no
    first-seen-pair set they would compute age zero and fire on every high-value
    payment — a control that declines everything is as broken as one that declines
    nothing."""
    from services.decision_service.app import context, evaluate as ev_mod
    from services.decision_service.app.counters import MemoryCounterStore

    store = MemoryCounterStore()
    store.write("t1", "A1", {"baseline_mean_paise": 1000.0})
    rules = [{"rule_id": "CPT-01", "family": "CPT", "threshold": 24, "comparator": "lte"}]
    ev = ev_mod.evaluate(rules=rules,
                         request={"debtor_account": "A1", "creditor_account": "B1",
                                  "amount_paise": 5_000_000, "rail": "UPI"},
                         store=store, tenant_id="t1", budget_ms=100)
    assert ev.matched == [], "CPT-01 must not fire from an absent pair set"
    assert "CPT-01" in context.NEEDS_SET_STORE


def test_chn01_is_only_taken_from_the_channel_never_derived():
    """Derived from empty device/counterparty sets it would count every payment as
    new-device-and-new-payee and fire constantly."""
    from services.decision_service.app import evaluate as ev_mod
    from services.decision_service.app.counters import MemoryCounterStore

    store = MemoryCounterStore()
    store.write("t1", "A1", {"baseline_mean_paise": 1000.0})
    rules = [{"rule_id": "CHN-01", "family": "CHN", "threshold": 3, "comparator": "gte"}]
    base = {"debtor_account": "A1", "creditor_account": "B1",
            "amount_paise": 5_000_000, "rail": "UPI", "device_id": "D1"}

    without = ev_mod.evaluate(rules=rules, request=dict(base), store=store,
                              tenant_id="t1", budget_ms=100)
    assert without.matched == [], "no channel signal means unmeasured, not derived"
    assert without.considered == 0

    with_signal = ev_mod.evaluate(rules=rules, request={**base, "risk_signals": 5},
                                  store=store, tenant_id="t1", budget_ms=100)
    assert [m["rule_id"] for m in with_signal.matched] == ["CHN-01"]


def test_chn04_and_chn05_reach_lane_a_when_the_channel_sends_them():
    """Before this, device_risk_score/behavior_anomaly_score reached Lane B (the batch
    detection engine) but decide.py never threaded them into the txn dict evaluate() reads
    - so Lane A could never see them no matter what a caller sent. Same shape as CHN-01/
    risk_signals: absent is unmeasured, present is scored, never derived or defaulted."""
    from services.decision_service.app import evaluate as ev_mod
    from services.decision_service.app.counters import MemoryCounterStore

    store = MemoryCounterStore()
    store.write("t1", "A1", {"baseline_mean_paise": 1000.0})
    rules = [{"rule_id": "CHN-04", "family": "CHN", "threshold": 0.7, "comparator": "gte"},
             {"rule_id": "CHN-05", "family": "CHN", "threshold": 0.7, "comparator": "gte"}]
    base = {"debtor_account": "A1", "creditor_account": "B1",
            "amount_paise": 5_000_000, "rail": "UPI"}

    without = ev_mod.evaluate(rules=rules, request=dict(base), store=store,
                              tenant_id="t1", budget_ms=100)
    assert without.matched == [], "no channel signal means unmeasured, not scored clean"

    with_signals = ev_mod.evaluate(
        rules=rules, request={**base, "device_risk_score": 0.91, "behavior_anomaly_score": 0.83},
        store=store, tenant_id="t1", budget_ms=100)
    assert {m["rule_id"] for m in with_signals.matched} == {"CHN-04", "CHN-05"}

    below_threshold = ev_mod.evaluate(
        rules=rules, request={**base, "device_risk_score": 0.2, "behavior_anomaly_score": 0.1},
        store=store, tenant_id="t1", budget_ms=100)
    assert below_threshold.matched == [], "measured but below threshold must not fire"


def test_the_catalogue_is_never_fetched_on_the_decision_path():
    """An HTTP call inside a payment window is the one thing this lane exists to avoid.
    A miss schedules a background reload and returns what is held."""
    import inspect
    from services.decision_service.app import store as store_mod
    src = inspect.getsource(store_mod.catalogue_for)
    assert "httpx" not in src, "catalogue_for must not fetch"
    assert "_schedule_refresh" in src


# --------------------------------------------------------- BR-316: policy persistence
def test_the_decision_policy_is_never_fetched_on_the_decision_path():
    """Same invariant as the catalogue, and for the same reason - what a rail actually
    enforces matters at least as much as which rules run."""
    import inspect
    from services.decision_service.app import store as store_mod
    src = inspect.getsource(store_mod.policy_for)
    assert "httpx" not in src, "policy_for must not fetch"
    assert "_schedule_policy_refresh" in src


@pytest.fixture(autouse=True)
def _clean_store_state():
    """store.py holds process-global state; each test starts from a clean slate and
    leaves one, so ordering never matters."""
    from services.decision_service.app import store as store_mod
    store_mod.invalidate()
    yield
    store_mod.invalidate()


def test_fetch_decision_policy_returns_none_when_nothing_is_active(monkeypatch):
    from services.decision_service.app import store as store_mod

    class _Resp:
        def raise_for_status(self):
            pass

        def json(self):
            return {"tenant_id": "t1", "available": False}

    monkeypatch.setattr(store_mod.httpx, "get", lambda *a, **kw: _Resp())
    assert store_mod._fetch_decision_policy("t1") is None


def test_fetch_decision_policy_parses_the_active_body(monkeypatch):
    from services.decision_service.app import store as store_mod

    class _Resp:
        def raise_for_status(self):
            pass

        def json(self):
            return {"tenant_id": "t1", "available": True, "version": "1.0.0",
                    "body": {"rails": {"UPI": {"mode": "inline", "budget_ms": 80,
                                               "fail": "open", "actions": ["decline"],
                                               "shadow": False}}}}

    monkeypatch.setattr(store_mod.httpx, "get", lambda *a, **kw: _Resp())
    pol = store_mod._fetch_decision_policy("t1")
    assert pol is not None
    rp = pol.for_rail("UPI")
    assert rp is not None
    assert rp.shadow is False


def test_refresh_policy_keeps_the_previous_copy_on_fetch_failure(monkeypatch):
    from services.decision_service.app import store as store_mod

    good = store_mod.policy_mod.parse("t1", {
        "rails": {"UPI": {"mode": "inline", "budget_ms": 80, "fail": "open",
                          "actions": ["decline"], "shadow": True}}})
    store_mod._STORED_POLICIES["t1"] = good

    def boom(tid):
        raise RuntimeError("config-service unreachable")

    monkeypatch.setattr(store_mod, "_fetch_decision_policy", boom)
    result = store_mod.refresh_policy("t1")
    assert result is good, "a fetch failure must not discard the last known-good policy"


def test_policy_for_falls_back_to_starter_when_nothing_stored(monkeypatch):
    from services.decision_service.app import store as store_mod

    monkeypatch.setattr(store_mod, "_fetch_decision_policy", lambda tid: None)
    pol = store_mod.policy_for("t-starter")
    rp = pol.for_rail("UPI")
    assert rp is not None
    assert rp.shadow is True, "the starter policy ships every rail in shadow mode"


def test_policy_for_prefers_a_stored_policy_over_starter():
    from services.decision_service.app import store as store_mod

    custom = store_mod.policy_mod.parse("t2", {
        "rails": {"UPI": {"mode": "inline", "budget_ms": 80, "fail": "open",
                          "actions": ["decline"], "shadow": False}}})
    store_mod._STORED_POLICIES["t2"] = custom
    store_mod._POLICY_GENERATIONS["t2"] = -1  # matches whatever the cache reports below
    cache = store_mod._policy_gen_cache()
    if cache is not None:
        store_mod._POLICY_GENERATIONS["t2"] = cache.current_generation()

    pol = store_mod.policy_for("t2")
    assert pol.for_rail("UPI").shadow is False


def test_an_explicit_override_always_wins_over_a_stored_policy():
    from services.decision_service.app import store as store_mod

    stored = store_mod.policy_mod.parse("t3", {
        "rails": {"UPI": {"mode": "inline", "budget_ms": 80, "fail": "open",
                          "actions": ["decline"], "shadow": False}}})
    store_mod._STORED_POLICIES["t3"] = stored
    cache = store_mod._policy_gen_cache()
    store_mod._POLICY_GENERATIONS["t3"] = (
        cache.current_generation() if cache is not None else -1)

    store_mod.set_policy("t3", {
        "rails": {"UPI": {"mode": "inline", "budget_ms": 80, "fail": "open",
                          "actions": ["decline"], "shadow": True}}})
    assert store_mod.policy_for("t3").for_rail("UPI").shadow is True, \
        "an explicit override must win even though a stored policy also exists"

    store_mod.clear_override("t3")
    assert store_mod.policy_for("t3").for_rail("UPI").shadow is False, \
        "clearing the override must reveal the stored policy underneath"


def test_invalidate_clears_the_stored_policy_state():
    from services.decision_service.app import store as store_mod

    store_mod._STORED_POLICIES["t4"] = store_mod.policy_mod.parse(
        "t4", store_mod.policy_mod.STARTER)
    store_mod._POLICY_GENERATIONS["t4"] = 3
    store_mod.invalidate("t4")
    assert "t4" not in store_mod._STORED_POLICIES
    assert "t4" not in store_mod._POLICY_GENERATIONS
