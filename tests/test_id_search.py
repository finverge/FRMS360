"""id_search: free-text substring filter backing the row tables' search boxes.

Mirrors test_mule_risk_indicators.py's approach - no hand-picked fixture, just a real
row pulled from the seeded corpus, so the assertion tracks whatever the seed actually
contains rather than a number that would drift.
"""


def _first_row(analytics_client, tid, headers, entity):
    return analytics_client.get(
        f"/analytics/{tid}/drill/{entity}", headers=headers,
        params={"limit": 1, "reveal": "true", "justification": "test fixture setup"},
    ).json()["rows"][0]


def test_id_search_finds_transaction_by_txn_id_substring(analytics_client, token_for, tid):
    h = token_for("investigator")
    row = _first_row(analytics_client, tid, h, "transaction")
    substring = row["txn_id"][2:-2] or row["txn_id"]

    r = analytics_client.get(
        f"/analytics/{tid}/drill/transaction", headers=h,
        params={"id_search": substring, "limit": 200},
    )
    assert r.status_code == 200, r.text
    ids = {x["txn_id"] for x in r.json()["rows"]}
    assert row["txn_id"] in ids


def test_id_search_finds_transaction_by_account_substring(analytics_client, token_for, tid):
    h = token_for("investigator")
    row = _first_row(analytics_client, tid, h, "transaction")
    account = row["debtor_account"]

    r = analytics_client.get(
        f"/analytics/{tid}/drill/transaction", headers=h,
        params={"id_search": account, "limit": 200, "reveal": "true", "justification": "test fixture setup"},
    )
    assert r.status_code == 200, r.text
    rows = r.json()["rows"]
    assert rows, "searching by the account that owns this row must return at least it"
    assert all(account in x["debtor_account"] or account in x["creditor_account"] for x in rows)


def test_id_search_finds_alert_by_alert_id_substring(analytics_client, token_for, tid):
    h = token_for("investigator")
    row = _first_row(analytics_client, tid, h, "alert")
    substring = row["alert_id"][2:-2] or row["alert_id"]

    r = analytics_client.get(
        f"/analytics/{tid}/drill/alert", headers=h,
        params={"id_search": substring, "limit": 200},
    )
    assert r.status_code == 200, r.text
    ids = {x["alert_id"] for x in r.json()["rows"]}
    assert row["alert_id"] in ids


def test_id_search_finds_case_by_case_id_substring(analytics_client, token_for, tid):
    h = token_for("investigator")
    row = _first_row(analytics_client, tid, h, "case")
    substring = row["case_id"][2:-2] or row["case_id"]

    r = analytics_client.get(
        f"/analytics/{tid}/drill/case", headers=h,
        params={"id_search": substring, "limit": 200},
    )
    assert r.status_code == 200, r.text
    ids = {x["case_id"] for x in r.json()["rows"]}
    assert row["case_id"] in ids


def test_id_search_no_match_is_empty_not_error(analytics_client, token_for, tid):
    h = token_for("investigator")
    r = analytics_client.get(
        f"/analytics/{tid}/drill/transaction", headers=h,
        params={"id_search": "ZZZZ-DOES-NOT-EXIST-ZZZZ"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["rows"] == []


def test_id_search_combines_with_other_filters(analytics_client, token_for, tid):
    """A search box in the console applies on top of whatever date/severity filters the
    dashboard already has active - not instead of them."""
    h = token_for("investigator")
    row = _first_row(analytics_client, tid, h, "transaction")

    r = analytics_client.get(
        f"/analytics/{tid}/drill/transaction", headers=h,
        params={"id_search": row["txn_id"], "regions": ["a-region-that-does-not-exist"]},
    )
    assert r.status_code == 200, r.text
    assert r.json()["rows"] == [], "an impossible region filter must still exclude the id_search match"
