from services.lane_c_service.app.signals.shareholding_check import compute_shareholding_check


def test_no_company_identifier_is_unmeasurable():
    result = compute_shareholding_check(
        None, {"attributes": {"promoter_stake_pct": 60, "encumbered_pct": 10}}, None)
    assert result.status == "unmeasurable"


def test_no_feed_entry_is_unmeasurable():
    result = compute_shareholding_check("U12345MH2020PTC000001", None, None)
    assert result.status == "unmeasurable"


def test_malformed_entry_missing_a_key_is_unmeasurable():
    result = compute_shareholding_check(
        "U12345MH2020PTC000001", {"attributes": {"promoter_stake_pct": 60}}, None)
    assert result.status == "unmeasurable"


def test_low_encumbrance_no_prior_passes():
    result = compute_shareholding_check(
        "U12345MH2020PTC000001",
        {"attributes": {"promoter_stake_pct": 60, "encumbered_pct": 10}}, None)
    assert result.status == "pass"
    assert result.evidence_basis == "shareholding"


def test_high_encumbrance_fires_even_with_no_prior():
    result = compute_shareholding_check(
        "U12345MH2020PTC000001",
        {"attributes": {"promoter_stake_pct": 60, "encumbered_pct": 75}}, None)
    assert result.status != "pass"
    assert result.status != "unmeasurable"
    assert result.signal_code == "LNC-19"
    assert "encumbered" in result.evidence.lower()


def test_a_stake_drop_since_the_prior_review_fires():
    result = compute_shareholding_check(
        "U12345MH2020PTC000001",
        {"attributes": {"promoter_stake_pct": 50, "encumbered_pct": 5}}, 60.0)
    assert result.status != "pass"
    assert result.status != "unmeasurable"
    assert "fell from" in result.evidence.lower()


def test_a_small_stake_wobble_does_not_fire():
    result = compute_shareholding_check(
        "U12345MH2020PTC000001",
        {"attributes": {"promoter_stake_pct": 59.0, "encumbered_pct": 5}}, 60.0)
    assert result.status == "pass"


def test_encumbrance_plus_stake_drop_compounds_severity():
    encumbrance_only = compute_shareholding_check(
        "X", {"attributes": {"promoter_stake_pct": 50, "encumbered_pct": 75}}, None)
    both = compute_shareholding_check(
        "X", {"attributes": {"promoter_stake_pct": 50, "encumbered_pct": 75}}, 60.0)
    assert both.severity > encumbrance_only.severity
