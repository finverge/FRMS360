"""Transaction geolocation: a real IP-geolocation lookup (ip-api.com, free tier) against
the transaction's own real IP - there is no lat/long field in the data model, so this
always needs the unmasked address.
"""


def _a_txn_with_public_ip(analytics_client, tid, headers) -> str:
    """The seed data mixes private-range demo IPs (10.x) with realistic public ones -
    find one of the latter so the "locatable" happy path has something real to resolve."""
    rows = analytics_client.get(
        f"/analytics/{tid}/drill/transaction", headers=headers,
        params={"limit": 200, "reveal": "true", "justification": "test fixture setup"},
    ).json()["rows"]
    for row in rows:
        if row["ip_addr"] and not row["ip_addr"].startswith("10."):
            return row["txn_id"]
    raise AssertionError("no transaction with a non-private IP found in the seed window")


def test_geolocate_requires_reveal(analytics_client, token_for, tid):
    h = token_for("investigator")
    txn_id = _a_txn_with_public_ip(analytics_client, tid, h)
    r = analytics_client.post(f"/analytics/{tid}/geolocate/{txn_id}", headers=h)
    assert r.status_code == 400, r.text
    assert r.json()["error"]["code"] == "reveal_required"


def test_geolocate_resolves_a_real_public_ip(analytics_client, token_for, tid):
    h = token_for("investigator")
    txn_id = _a_txn_with_public_ip(analytics_client, tid, h)
    r = analytics_client.post(
        f"/analytics/{tid}/geolocate/{txn_id}", headers=h,
        params={"reveal": "true", "justification": "test fixture setup"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["locatable"] is True
    assert -90 <= body["lat"] <= 90
    assert -180 <= body["lon"] <= 180
    assert body["country"]


def test_geolocate_private_ip_is_not_locatable_not_an_error(analytics_client, token_for, tid):
    """The demo multi-hub case's transactions use 10.x addresses - a private range no
    geolocation service can resolve. That must come back as an honest 'not locatable',
    not a 500 and not fabricated coordinates."""
    h = token_for("investigator")
    rows = analytics_client.get(
        f"/analytics/{tid}/drill/transaction", headers=h,
        params={"limit": 200, "reveal": "true", "justification": "test fixture setup"},
    ).json()["rows"]
    private_txn = next((row["txn_id"] for row in rows if row["ip_addr"].startswith("10.")), None)
    if not private_txn:
        return  # seed data varies; this case is opportunistic, not load-bearing

    r = analytics_client.post(
        f"/analytics/{tid}/geolocate/{private_txn}", headers=h,
        params={"reveal": "true", "justification": "test fixture setup"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["locatable"] is False
    assert body["lat"] is None


def test_geolocate_unknown_transaction_is_404(analytics_client, token_for, tid):
    h = token_for("investigator")
    r = analytics_client.post(
        f"/analytics/{tid}/geolocate/TDOESNOTEXIST999", headers=h,
        params={"reveal": "true", "justification": "test fixture setup"},
    )
    assert r.status_code == 404, r.text
