from services.lane_c_service.app.signals.stock_audit_check import compute_stock_audit_check


def test_no_company_identifier_is_unmeasurable():
    result = compute_stock_audit_check(None, {"attributes": {"has_critical_issue": True}})
    assert result.status == "unmeasurable"


def test_no_feed_entry_is_unmeasurable():
    result = compute_stock_audit_check("U12345MH2020PTC000001", None)
    assert result.status == "unmeasurable"


def test_malformed_entry_missing_the_key_is_unmeasurable():
    result = compute_stock_audit_check(
        "U12345MH2020PTC000001", {"attributes": {"detail": "x"}})
    assert result.status == "unmeasurable"


def test_no_critical_issue_passes():
    result = compute_stock_audit_check(
        "U12345MH2020PTC000001", {"attributes": {"has_critical_issue": False}})
    assert result.status == "pass"
    assert result.evidence_basis == "stock feed"


def test_a_critical_issue_fires():
    result = compute_stock_audit_check(
        "U12345MH2020PTC000001",
        {"attributes": {"has_critical_issue": True, "detail": "Stock shortfall of 30%."}})
    assert result.status != "pass"
    assert result.status != "unmeasurable"
    assert result.signal_code == "LNC-18"
    assert "Stock shortfall" in result.evidence
