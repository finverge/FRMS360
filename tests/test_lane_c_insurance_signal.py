from services.lane_c_service.app.signals.insurance_check import compute_insurance_coverage_check


def test_no_company_identifier_is_unmeasurable():
    result = compute_insurance_coverage_check(
        None, {"attributes": {"insured_value_paise": 100}}, 100)
    assert result.status == "unmeasurable"


def test_no_feed_entry_is_unmeasurable():
    result = compute_insurance_coverage_check("U12345MH2020PTC000001", None, 100)
    assert result.status == "unmeasurable"


def test_no_inventory_figure_is_unmeasurable():
    result = compute_insurance_coverage_check(
        "U12345MH2020PTC000001", {"attributes": {"insured_value_paise": 100}}, None)
    assert result.status == "unmeasurable"


def test_malformed_entry_missing_the_key_is_unmeasurable():
    result = compute_insurance_coverage_check(
        "U12345MH2020PTC000001", {"attributes": {"policy_number": "X"}}, 100)
    assert result.status == "unmeasurable"


def test_reasonable_cover_passes():
    result = compute_insurance_coverage_check(
        "U12345MH2020PTC000001",
        {"attributes": {"insured_value_paise": 90, "policy_number": "P1"}}, 100)
    assert result.status == "pass"
    assert result.evidence_basis == "ratio"


def test_under_insured_fires():
    result = compute_insurance_coverage_check(
        "U12345MH2020PTC000001",
        {"attributes": {"insured_value_paise": 50, "policy_number": "P1"}}, 100)
    assert result.status != "pass"
    assert result.status != "unmeasurable"
    assert result.signal_code == "LNC-17"
    assert "under-insured" in result.evidence.lower()


def test_over_insured_fires():
    result = compute_insurance_coverage_check(
        "U12345MH2020PTC000001",
        {"attributes": {"insured_value_paise": 200, "policy_number": "P1"}}, 100)
    assert result.status != "pass"
    assert result.status != "unmeasurable"
    assert "over-insured" in result.evidence.lower()
