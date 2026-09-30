"""Credit health scorer - aggregate deduction logic, and one full borrower-quarter
scenario inspired by the design doc's Use Case 3 (MSME accounting-manipulation).

Use Case 3's own narrative numbers were written as illustrative prose, not built to
satisfy the playbook's precise per-signal formulas (its WC-borrowing paragraph assumes a
different trigger shape than the playbook's own fully-specified WC_BLOAT algorithm this
engine implements) - so this constructs a scenario in the same spirit (accounting-policy
change + working-capital deterioration hidden behind reported earnings growth) using
numbers that do satisfy this engine's actual, documented conditions, rather than
asserting a byte-exact reproduction of the narrative's numbers.
"""
from services.lane_c_service.app.scoring import credit_health_scorer as scorer
from services.lane_c_service.app.signals import signal_engine as se


def test_no_signals_fired_scores_100():
    signals = se.compute_all({"REVENUE": 100_00_00000, "INVENTORY": 20_00_00000},
                             {"REVENUE": 100_00_00000, "INVENTORY": 20_00_00000})
    result = scorer.score(signals)
    assert result.score_value == 100
    assert result.recommendation == "continue"
    assert result.signal_count == 0


def test_unmeasurable_signals_are_excluded_not_penalised():
    # First-ever statement: every ratio signal is unmeasurable, LNC-01/08 still compute.
    signals = se.compute_all({"REVENUE": 100_00_00000}, None)
    result = scorer.score(signals)
    # Only LNC-01 and LNC-08 are measurable here (both clean, no notes/history) - score
    # must not be penalised for the five ratios that simply have no prior period yet.
    assert result.score_value == 100
    assert result.signal_count == 0


def test_a_single_critical_signal_deducts_twenty():
    prior = {"INVENTORY": 20_00_00000, "REVENUE": 100_00_00000}
    cur = {"INVENTORY": 35_00_00000, "REVENUE": 95_00_00000}  # LNC-03 critical, see test_lane_c_signals
    signals = se.compute_all(cur, prior)
    result = scorer.score(signals)
    assert result.score_value == 80
    assert result.critical_count == 1
    assert result.recommendation == "continue"  # 80 is the >=80 boundary, inclusive


def test_trend_deteriorating_when_score_drops_more_than_five():
    prior = {"INVENTORY": 20_00_00000, "REVENUE": 100_00_00000}
    cur = {"INVENTORY": 35_00_00000, "REVENUE": 95_00_00000}  # fires LNC-03 critical, -20
    signals = se.compute_all(cur, prior)
    result = scorer.score(signals, prior_score=90)
    assert result.score_value == 80
    assert result.trend == "deteriorating"


def test_trend_stable_within_a_five_point_band():
    result = scorer.score({}, prior_score=98)  # score=100, within 5 of prior
    assert result.trend == "stable"


def test_trend_new_with_no_prior_score():
    result = scorer.score({})
    assert result.trend == "new"


# ------------------------------------------------------------------ Use Case 3-inspired
def test_msme_style_deterioration_lands_in_escalate_band():
    """Revenue and EBITDA both nearly flat (the design doc's borrower reports them as
    growing via the accounting change caught by LNC-08 - the point of the scenario),
    while working-capital borrowing and receivables both balloon underneath: three real,
    independently fired signals a credit analyst should see even though the top-line
    numbers look stable."""
    prior = {
        "REVENUE": 30_00_00000, "AR": 3_00_00000, "WC_BORROWING": 5_00_00000,
        "EBITDA": 6_00_00000, "INVENTORY": 4_00_00000, "OCA": 1_00_00000,
    }
    cur = {
        "REVENUE": 30_50_00000, "AR": 9_00_00000, "WC_BORROWING": 9_00_00000,
        "EBITDA": 6_10_00000, "INVENTORY": 4_20_00000, "OCA": 1_10_00000,
    }
    prior_notes = "Depreciation is provided on a straight-line basis over useful life."
    cur_notes = "Depreciation is provided using the written-down value (WDV) method."

    signals = se.compute_all(cur, prior, cur_notes=cur_notes, prior_notes=prior_notes,
                             cur_reporting_month=3, prior_reporting_month=3)

    assert signals["LNC-08"].status != "pass"  # accounting-policy change caught
    assert signals["LNC-04"].status == "critical"  # AR tripled vs. ~2% revenue growth
    assert signals["LNC-06"].status == "critical"  # WC borrowing +80% despite flat EBITDA

    result = scorer.score(signals)
    assert result.score_value <= 55
    assert result.recommendation == "investigate"
