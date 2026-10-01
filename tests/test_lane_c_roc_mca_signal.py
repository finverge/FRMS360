from services.lane_c_service.app.signals.roc_mca_check import compute_roc_mca_check


def test_no_company_identifier_is_unmeasurable():
    result = compute_roc_mca_check(None, {"attributes": {"has_undisclosed_liability": True}})
    assert result.status == "unmeasurable"


def test_no_feed_entry_is_unmeasurable():
    result = compute_roc_mca_check("U12345MH2020PTC000001", None)
    assert result.status == "unmeasurable"


def test_malformed_entry_missing_the_key_is_unmeasurable():
    result = compute_roc_mca_check("U12345MH2020PTC000001", {"attributes": {"detail": "x"}})
    assert result.status == "unmeasurable"


def test_no_undisclosed_liability_passes():
    result = compute_roc_mca_check(
        "U12345MH2020PTC000001",
        {"attributes": {"has_undisclosed_liability": False}})
    assert result.status == "pass"


def test_an_undisclosed_liability_fires_critical():
    result = compute_roc_mca_check(
        "U12345MH2020PTC000001",
        {"attributes": {"has_undisclosed_liability": True,
                        "detail": "Unpaid charge with a private lender."}})
    assert result.status == "critical"
    assert result.signal_code == "LNC-10"
    assert "Unpaid charge" in result.evidence
    assert result.evidence_basis == "mca_roc feed"
