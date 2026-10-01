from services.lane_c_service.app.signals.enforcement_check import (
    compute_enforcement_action_check,
)


def test_no_company_identifier_is_unmeasurable():
    result = compute_enforcement_action_check(None, None, True)
    assert result.status == "unmeasurable"


def test_no_feed_loaded_at_all_is_unmeasurable():
    result = compute_enforcement_action_check("U12345MH2020PTC000001", None, False)
    assert result.status == "unmeasurable"


def test_feed_loaded_but_no_entry_for_this_borrower_passes():
    """Distinguishes "no feed" (unmeasurable) from "feed loaded, this borrower isn't in
    it" (a real, positive absence of an enforcement action)."""
    result = compute_enforcement_action_check("U12345MH2020PTC000001", None, True)
    assert result.status == "pass"
    assert result.evidence_basis == "enforcement"


def test_an_entry_itself_is_the_finding():
    result = compute_enforcement_action_check(
        "U12345MH2020PTC000001",
        {"attributes": {"authority": "GST", "action_type": "raid", "date": "2026-05-01"}},
        True)
    assert result.status != "pass"
    assert result.status != "unmeasurable"
    assert result.signal_code == "LNC-20"
    assert "GST" in result.evidence
    assert "raid" in result.evidence
