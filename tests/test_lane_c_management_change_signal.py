from services.lane_c_service.app.signals.management_change_check import (
    compute_management_change_check,
)


def test_no_company_identifier_is_unmeasurable():
    result = compute_management_change_check(
        None, {"attributes": {"changes_12m": 3}})
    assert result.status == "unmeasurable"


def test_no_feed_entry_is_unmeasurable():
    result = compute_management_change_check("U12345MH2020PTC000001", None)
    assert result.status == "unmeasurable"


def test_malformed_entry_missing_the_key_is_unmeasurable():
    result = compute_management_change_check(
        "U12345MH2020PTC000001", {"attributes": {"detail": "x"}})
    assert result.status == "unmeasurable"


def test_a_single_change_in_a_year_passes():
    result = compute_management_change_check(
        "U12345MH2020PTC000001", {"attributes": {"changes_12m": 1}})
    assert result.status == "pass"
    assert result.evidence_basis == "mca_roc feed"


def test_frequent_changes_fire():
    result = compute_management_change_check(
        "U12345MH2020PTC000001",
        {"attributes": {"changes_12m": 3, "detail": "CFO and two independent directors "
                                                      "resigned within the quarter."}})
    assert result.status != "pass"
    assert result.status != "unmeasurable"
    assert result.signal_code == "LNC-23"
    assert "CFO" in result.evidence


def test_more_changes_compound_to_higher_severity():
    two = compute_management_change_check(
        "X", {"attributes": {"changes_12m": 2}})
    four = compute_management_change_check(
        "X", {"attributes": {"changes_12m": 4}})
    assert four.severity > two.severity
