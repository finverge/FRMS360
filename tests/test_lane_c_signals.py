"""The eight LNC signal algorithms - pure math, no I/O, same style as
test_cbs_events.py's ratio tests: known inputs, hand-checked expected outputs.

The recurring invariant here is the same one cbs_features.py's tests already establish
for the payment side: a ratio with no prior period to compare against is unmeasurable,
never a fabricated zero or a false "pass".
"""
import pytest

from services.lane_c_service.app.signals import signal_engine as se


# ------------------------------------------------------------------ prior-period guard
def test_every_ratio_signal_is_unmeasurable_with_no_prior_period():
    cur = {"INVENTORY": 100, "REVENUE": 100, "AR": 100, "OCA": 100,
           "WC_BORROWING": 100, "FIXED_ASSETS": 100}
    assert se.compute_inventory_movement(cur, None).status == "unmeasurable"
    assert se.compute_receivables_movement(cur, None).status == "unmeasurable"
    assert se.compute_oca_change(cur, None).status == "unmeasurable"
    assert se.compute_working_capital_bloat(cur, None).status == "unmeasurable"
    assert se.compute_fixed_asset_funding(cur, None).status == "unmeasurable"


def test_a_zero_prior_denominator_is_unmeasurable_not_infinite():
    cur = {"INVENTORY": 100, "REVENUE": 100}
    prior = {"INVENTORY": 0, "REVENUE": 100}
    assert se.compute_inventory_movement(cur, prior).status == "unmeasurable"


# ------------------------------------------------------------------ LNC-03 inventory
def test_inventory_buildup_against_falling_revenue_is_critical():
    # 2022-23: Revenue 100cr, Inventory 20cr. 2023-24: Revenue 95cr (-5%), Inventory 35cr (+75%).
    prior = {"INVENTORY": 20_00_00000, "REVENUE": 100_00_00000}
    cur = {"INVENTORY": 35_00_00000, "REVENUE": 95_00_00000}
    r = se.compute_inventory_movement(cur, prior)
    assert r.observed_value == pytest.approx(0.75)
    assert r.baseline_value == pytest.approx(-0.05)
    # revenue down + inv up >10% (+30), inv growth >50% (+25) = 55 -> critical
    assert r.severity == 55
    assert r.status == "critical"


def test_inventory_growing_with_revenue_is_clean():
    prior = {"INVENTORY": 20_00_00000, "REVENUE": 100_00_00000}
    cur = {"INVENTORY": 21_00_00000, "REVENUE": 110_00_00000}
    r = se.compute_inventory_movement(cur, prior)
    assert r.status == "pass"
    assert r.severity == 0


def test_inventory_peer_overlay_only_applies_when_a_cohort_is_supplied():
    prior = {"INVENTORY": 20_00_00000, "REVENUE": 100_00_00000}
    cur = {"INVENTORY": 22_00_00000, "REVENUE": 105_00_00000}  # 10% inv growth, clean otherwise
    without_peer = se.compute_inventory_movement(cur, prior, peer_median_growth=None)
    with_low_peer = se.compute_inventory_movement(cur, prior, peer_median_growth=-0.2)
    assert without_peer.severity == 0
    assert with_low_peer.severity == 15  # 0.10 > -0.2 + 0.2 = 0.0 -> peer overlay fires


# ------------------------------------------------------------------ LNC-04 receivables
def test_receivables_doubling_dso_is_critical():
    # Design doc Use Case 1: Revenue flat 45cr, AR 8cr -> 16cr (+100%), DSO 65 -> 130.
    prior = {"AR": 8_00_00000, "REVENUE": 45_00_00000}
    cur = {"AR": 16_00_00000, "REVENUE": 45_00_00000}
    r = se.compute_receivables_movement(cur, prior)
    assert r.observed_value == pytest.approx(1.0)
    # ar_growth>0.3 & rev_growth<0.1 (+35); dso jump ~65d->130d, >20d jump (+25);
    # ar/rev = 16/45 = 0.356, not >0.4, no +15. Total 60 -> critical.
    assert r.severity == 60
    assert r.status == "critical"


# ------------------------------------------------------------------ LNC-05 OCA
def test_oca_ballooning_is_critical():
    # Design doc: OCA 5cr in 2023 -> 25cr in 2024 (+400%).
    prior = {"OCA": 5_00_00000, "REVENUE": 100_00_00000}
    cur = {"OCA": 25_00_00000, "REVENUE": 100_00_00000}
    r = se.compute_oca_change(cur, prior)
    # growth 4.0 > 1.0 (+30); oca/rev = 0.25 > 0.15 (+15). Total 45 -> high.
    assert r.severity == 45
    assert r.status == "high"


# ------------------------------------------------------------------ LNC-06 WC bloat
def test_wc_borrowing_up_with_flat_revenue_and_ebitda_is_critical():
    prior = {"WC_BORROWING": 20_00_00000, "REVENUE": 100_00_00000, "EBITDA": 15_00_00000}
    cur = {"WC_BORROWING": 40_00_00000, "REVENUE": 100_00_00000, "EBITDA": 15_00_00000}
    r = se.compute_working_capital_bloat(cur, prior)
    # wc_growth 1.0>0.2, rev_growth 0<0.1, ebitda_growth 0<0.05 (+40);
    # wc_pct: 0.40 vs 0.20, delta 0.20>0.10 (+20); wc_pct_cur 0.40>0.30 (+15). Total 75.
    assert r.severity == 75
    assert r.status == "critical"


def test_wc_bloat_degrades_gracefully_when_ebitda_is_absent():
    prior = {"WC_BORROWING": 20_00_00000, "REVENUE": 100_00_00000}
    cur = {"WC_BORROWING": 40_00_00000, "REVENUE": 100_00_00000}
    r = se.compute_working_capital_bloat(cur, prior)
    assert r.status != "unmeasurable"  # missing EBITDA alone must not block the signal
    assert r.severity >= 40


# ------------------------------------------------------------------ LNC-07 fixed assets
def test_unfunded_capex_is_critical():
    # Design doc: FA 50cr->75cr (+25cr capex), LT debt and equity both unchanged.
    prior = {"FIXED_ASSETS": 50_00_00000, "LT_DEBT": 30_00_00000, "EQUITY": 20_00_00000}
    cur = {"FIXED_ASSETS": 75_00_00000, "LT_DEBT": 30_00_00000, "EQUITY": 20_00_00000}
    r = se.compute_fixed_asset_funding(cur, prior)
    assert r.observed_value == pytest.approx(0.0)  # funded_ratio: 0 raised / 25cr capex
    # funded_ratio 0 < 0.5 (+40); funding_raised <=0 (+15). Total 55 -> critical.
    assert r.severity == 55
    assert r.status == "critical"


def test_fully_funded_capex_passes():
    prior = {"FIXED_ASSETS": 50_00_00000, "LT_DEBT": 30_00_00000, "EQUITY": 20_00_00000}
    cur = {"FIXED_ASSETS": 75_00_00000, "LT_DEBT": 45_00_00000, "EQUITY": 30_00_00000}
    r = se.compute_fixed_asset_funding(cur, prior)
    assert r.status == "pass"


def test_no_new_capex_passes_trivially():
    prior = {"FIXED_ASSETS": 50_00_00000, "LT_DEBT": 30_00_00000, "EQUITY": 20_00_00000}
    cur = {"FIXED_ASSETS": 48_00_00000, "LT_DEBT": 30_00_00000, "EQUITY": 20_00_00000}
    r = se.compute_fixed_asset_funding(cur, prior)
    assert r.status == "pass"
    assert r.severity == 0


# ------------------------------------------------------------------ LNC-01 statutory dues
def test_statutory_dues_language_is_flagged():
    notes = ("Notes to accounts: as of March 31, 2024, the company has unpaid statutory "
             "dues of Rs 2.3 crore to the Income Tax department, under dispute.")
    r = se.compute_statutory_dues_default(notes)
    assert r.status == "high"
    assert r.evidence_basis == "text-pattern"
    assert "statutory dues" in r.evidence.lower()


def test_clean_notes_pass_statutory_dues():
    r = se.compute_statutory_dues_default("Notes: no contingent liabilities of note this period.")
    assert r.status == "pass"


def test_no_notes_text_is_unmeasurable():
    r = se.compute_statutory_dues_default("")
    assert r.status == "unmeasurable"


# ------------------------------------------------------------------ LNC-08 accounting change
def test_depreciation_method_change_is_flagged():
    prior_notes = "Depreciation is provided on a straight-line basis over useful life."
    cur_notes = "Depreciation is provided on the written-down value (WDV) method."
    r = se.compute_accounting_change(cur_notes, prior_notes, cur_reporting_month=3,
                                     prior_reporting_month=3)
    assert r.status != "pass"
    assert r.evidence_basis == "structured+notes"


def test_fiscal_year_end_change_is_flagged():
    r = se.compute_accounting_change("", "", cur_reporting_month=12,
                                     prior_reporting_month=3)
    assert r.severity >= 35
    assert "year-end" in r.evidence.lower()


def test_unchanged_accounting_passes():
    notes = "Depreciation is provided on a straight-line basis over useful life."
    r = se.compute_accounting_change(notes, notes, cur_reporting_month=3,
                                     prior_reporting_month=3)
    assert r.status == "pass"


# ------------------------------------------------------------------ LNC-02 scope creep
def test_scope_creep_is_always_unmeasurable_today():
    r = se.compute_scope_creep()
    assert r.status == "unmeasurable"
    assert "project-appraisal" in r.evidence.lower()


# ------------------------------------------------------------------ compute_all
def test_compute_all_returns_exactly_the_eight_declared_signals():
    from services.lane_c_service.app.signal_catalogue import ALL_SIGNAL_IDS
    results = se.compute_all({"REVENUE": 100}, None)
    assert set(results) == set(ALL_SIGNAL_IDS)
    assert len(results) == 8


