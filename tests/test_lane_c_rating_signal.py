from services.lane_c_service.app.signals.rating_check import compute_rating_check


def test_no_company_identifier_is_unmeasurable():
    result = compute_rating_check(None, {"attributes": {"rank": 3, "outlook": "stable"}}, None)
    assert result.status == "unmeasurable"


def test_no_feed_entry_is_unmeasurable():
    result = compute_rating_check("U12345MH2020PTC000001", None, None)
    assert result.status == "unmeasurable"


def test_missing_rank_is_unmeasurable():
    result = compute_rating_check(
        "U12345MH2020PTC000001", {"attributes": {"outlook": "stable"}}, None)
    assert result.status == "unmeasurable"


def test_out_of_range_rank_is_unmeasurable():
    result = compute_rating_check(
        "U12345MH2020PTC000001", {"attributes": {"rank": 12, "outlook": "stable"}}, None)
    assert result.status == "unmeasurable"


def test_stable_outlook_with_no_prior_rank_passes():
    """A first-ever rating with nothing alarming about it: nothing to compare against,
    but also nothing currently wrong - pass, not unmeasurable, since real data exists."""
    result = compute_rating_check(
        "U12345MH2020PTC000001",
        {"attributes": {"rank": 3, "outlook": "stable", "grade": "A", "agency": "CRISIL"}},
        None)
    assert result.status == "pass"


def test_negative_outlook_fires_even_with_no_prior_rank():
    """Outlook is a single point-in-time fact - it doesn't need history to be a warning."""
    result = compute_rating_check(
        "U12345MH2020PTC000001",
        {"attributes": {"rank": 3, "outlook": "negative", "grade": "A", "agency": "CRISIL"}},
        None)
    assert result.status != "pass"
    assert result.status != "unmeasurable"
    assert "negative" in result.evidence.lower()


def test_a_downgrade_since_the_prior_review_fires():
    result = compute_rating_check(
        "U12345MH2020PTC000001",
        {"attributes": {"rank": 5, "outlook": "stable", "grade": "BB", "agency": "ICRA"}},
        3.0)  # prior rank was 3 (better); now 5 (worse) = downgrade
    assert result.status != "pass"
    assert result.status != "unmeasurable"
    assert "worse" in result.evidence.lower()


def test_an_improvement_since_the_prior_review_passes():
    result = compute_rating_check(
        "U12345MH2020PTC000001",
        {"attributes": {"rank": 2, "outlook": "stable", "grade": "AA", "agency": "ICRA"}},
        4.0)  # prior rank was 4 (worse); now 2 (better) = upgrade, not a downgrade
    assert result.status == "pass"


def test_downgrade_plus_negative_outlook_compounds_severity():
    downgrade_only = compute_rating_check(
        "X", {"attributes": {"rank": 5, "outlook": "stable"}}, 3.0)
    both = compute_rating_check(
        "X", {"attributes": {"rank": 5, "outlook": "negative"}}, 3.0)
    assert both.severity > downgrade_only.severity
