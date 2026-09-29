"""The two lanes must measure the same thing.

This is the test that makes a two-lane architecture safe. The near-real-time engine
builds an AccountContext with SQL; the inline lane rebuilds one from counters. If those
two paths ever produce different observations for the same transaction, a bank gets a
different answer depending on which lane saw the payment — and because the lanes see
different traffic, nobody would notice.

Both lanes call ``cp_common.observations.observe``. These tests assert that the *inputs*
survive the round trip through the counter store, so calling one function is actually
sufficient. Treat a failure here the way ``alembic check`` is treated: a build breaker,
not a flake.
"""
from datetime import datetime, timedelta, timezone

import pytest

from cp_common.observations import AccountContext, observe
from services.analytics_service.app.detection import counter_feed
from services.decision_service.app import context as inline_ctx

NOW = datetime(2026, 8, 4, 12, 0, tzinfo=timezone.utc)
HIGH_VALUE = 6_00_000 * 100          # above HIGH_VALUE_PAISE


def a_context() -> AccountContext:
    """A context with every reconstructable field set to a distinctive value."""
    return AccountContext(
        baseline_mean_paise=250_000.0,
        inflow_paise=9_000_00,
        outflow_paise=8_700_00,
        distinct_counterparties=46,
        small_credit_count=11,
        sub_ctr_count=6,
        near_upi_limit_count=5,
        device_cluster_size=4,
        last_seen=NOW - timedelta(days=200),
    )


def a_txn(**kw) -> dict:
    txn = {"txn_id": "T1", "ts": NOW, "rail": "UPI", "amount_paise": HIGH_VALUE,
           "debtor_account": "AC-DR", "creditor_account": "AC-CR"}
    txn.update(kw)
    return txn


def round_trip(ctx: AccountContext) -> AccountContext:
    """Context -> counters -> context, exactly as the two services would do it."""
    counters = counter_feed.extract(ctx, now=NOW)
    return inline_ctx.rebuild(counters, now=NOW)


# ------------------------------------------------------------- the round trip holds
def test_every_published_field_survives_the_round_trip():
    original = a_context()
    rebuilt = round_trip(original)
    for field in counter_feed.FIELDS:
        assert getattr(rebuilt, field) == pytest.approx(getattr(original, field)), \
            f"{field} did not survive context -> counters -> context"


def test_last_seen_survives_as_an_age():
    original = a_context()
    rebuilt = round_trip(original)
    assert rebuilt.last_seen is not None
    drift = abs((rebuilt.last_seen - original.last_seen).total_seconds())
    assert drift < 1.0, f"last_seen drifted by {drift}s through the counter store"


def test_an_account_with_no_history_rebuilds_empty_not_zeroed():
    """No counters means no context — not a context full of zeros that would score."""
    rebuilt = inline_ctx.rebuild({}, now=NOW)
    assert rebuilt.last_seen is None
    assert rebuilt.baseline_mean_paise == 0.0


# ----------------------------------------------------- the observations then agree
@pytest.mark.parametrize("subject", ["debtor", "creditor"])
def test_both_lanes_observe_identical_values(subject):
    """The whole point: one context, two construction paths, identical observations."""
    original = a_context()
    rebuilt = round_trip(original)

    batch_side = observe(a_txn(), original, {}, NOW, subject=subject)
    inline_side = observe(a_txn(), rebuilt, {}, NOW, subject=subject)

    assert set(batch_side) == set(inline_side), (
        f"lanes disagree about which indicators are measurable "
        f"({subject}): batch={sorted(batch_side)} inline={sorted(inline_side)}")
    for rule_id, value in batch_side.items():
        assert inline_side[rule_id] == pytest.approx(value, rel=1e-6), (
            f"{rule_id} differs between lanes on the {subject} side: "
            f"batch={value} inline={inline_side[rule_id]}")


@pytest.mark.parametrize("rail", ["UPI", "RTGS", "NEFT", "IMPS", "CARD"])
def test_the_lanes_agree_on_every_rail(rail):
    """Several observations are rail-conditional. Reimplementing those conditions in the
    inline lane is precisely the divergence this test exists to catch."""
    original = a_context()
    rebuilt = round_trip(original)
    txn = a_txn(rail=rail)
    assert observe(txn, original, {}, NOW) == observe(txn, rebuilt, {}, NOW)


@pytest.mark.parametrize("amount", [1_00, 4_99_999 * 100, HIGH_VALUE, 50_00_000 * 100])
def test_the_lanes_agree_across_the_high_value_boundary(amount):
    """Indicators switch on and off at the high-value threshold. Both sides must switch
    at the same point, or a payment just over the line scores differently by lane."""
    original = a_context()
    rebuilt = round_trip(original)
    txn = a_txn(amount_paise=amount)
    assert observe(txn, original, {}, NOW) == observe(txn, rebuilt, {}, NOW)


# --------------------------------------------------- what cannot be rebuilt is named
def test_pair_dependent_indicators_are_declared_unmeasurable_not_zeroed():
    """Beneficiary age needs a per-pair set the counter store does not hold. Scoring it
    zero would read as 'brand new beneficiary' and fire on every payment."""
    for rule_id in ("CPT-01", "VEL-02"):
        assert inline_ctx.unmeasurable(rule_id), \
            f"{rule_id} needs a set store and must say so"
        assert "not an account counter" in inline_ctx.unmeasurable(rule_id)


def test_a_rebuilt_context_never_invents_a_beneficiary_age():
    """The dangerous failure: an empty first_seen_pair making every payee look new."""
    rebuilt = round_trip(a_context())
    assert rebuilt.first_seen_pair == {}
    got = observe(a_txn(), rebuilt, {}, NOW)
    # observe treats an unseen pair as age zero, which is why the inline lane must
    # suppress these rules rather than trust the value.
    assert "CPT-01" in got and got["CPT-01"] == 0.0, (
        "if this changes, revisit inline_ctx.NEEDS_SET_STORE — the suppression exists "
        "because an unknown pair is indistinguishable from a brand-new one")


# --------------------------- AI/ML roadmap Phase 1 (device/behavioural biometrics)
def test_biometric_scores_are_measured_directly_from_the_transaction():
    """CHN-04/CHN-05 read straight off the transaction rather than AccountContext, so
    both lanes trivially agree as long as they pass the same transaction through -
    unlike every other indicator here, there is no counter-store round trip to test."""
    ctx = a_context()
    out = observe(a_txn(device_risk_score=0.9, behavior_anomaly_score=0.4), ctx, {}, NOW)
    assert out["CHN-04"] == pytest.approx(0.9)
    assert out["CHN-05"] == pytest.approx(0.4)


def test_biometric_scores_absent_on_the_transaction_stay_unmeasured():
    """No score on the payment is the normal case today - it must not read as zero risk,
    the same discipline every other unmeasurable indicator in this catalogue follows."""
    ctx = a_context()
    out = observe(a_txn(), ctx, {}, NOW)
    assert "CHN-04" not in out
    assert "CHN-05" not in out
