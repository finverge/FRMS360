"""Mule Risk Indicators: real aggregates over fact_transaction for one account.

No new fixture data - reuses the seeded corpus the same way test_analytics.py's own
graph/account360 tests do, so these assert against whatever the shared session tenant
actually contains rather than a hand-picked number that would drift with the seed.
"""


def _a_real_account(analytics_client, tid, headers) -> str:
    row = analytics_client.get(
        f"/analytics/{tid}/drill/transaction", headers=headers,
        params={"limit": 1, "reveal": "true", "justification": "test fixture setup"},
    ).json()["rows"][0]
    return row["debtor_account"]


def test_mule_risk_shape_and_totals(analytics_client, token_for, tid):
    h = token_for("investigator")
    account = _a_real_account(analytics_client, tid, h)

    r = analytics_client.get(f"/analytics/{tid}/mule-risk/{account}", headers=h)
    assert r.status_code == 200, r.text
    body = r.json()

    assert body["account"] == account
    assert body["total_txn_count"] > 0, "the account came from a real transaction row"

    linkage = body["network_linkage"]
    assert set(linkage) == {
        "linked_devices", "linked_accounts", "linked_ips",
        "distinct_branches", "distinct_regions",
    }
    assert all(isinstance(v, int) and v >= 0 for v in linkage.values())

    flow = body["transaction_flow"]
    assert set(flow["funds_in_txn_count"]) == {"1d", "1w", "1m"}
    assert set(flow["funds_out_txn_count"]) == {"1d", "1w", "1m"}

    velocity = body["velocity"]
    assert velocity["net_flow_paise"] == velocity["funds_in_paise"] - velocity["funds_out_paise"]


def test_mule_risk_windows_are_cumulative(analytics_client, token_for, tid):
    """1W and 1M each look further back than the window before it, so a transaction
    inside 1D is also inside 1W and 1M - the count can only grow, never shrink."""
    h = token_for("investigator")
    account = _a_real_account(analytics_client, tid, h)
    flow = analytics_client.get(
        f"/analytics/{tid}/mule-risk/{account}", headers=h
    ).json()["transaction_flow"]

    for direction in ("funds_in_txn_count", "funds_out_txn_count"):
        counts = flow[direction]
        assert counts["1d"] <= counts["1w"] <= counts["1m"], (direction, counts)


def test_mule_risk_unknown_account_is_all_zero(analytics_client, token_for, tid):
    """An account with no transactions must come back as real zeros, not an error -
    an investigator pasting a typo'd account number should see 'nothing here', not a 500."""
    h = token_for("investigator")
    r = analytics_client.get(
        f"/analytics/{tid}/mule-risk/ACDOESNOTEXIST999", headers=h
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["total_txn_count"] == 0
    assert all(v == 0 for v in body["network_linkage"].values())
    assert body["velocity"]["funds_in_paise"] == 0
    assert body["velocity"]["funds_out_paise"] == 0
    assert body["velocity"]["net_flow_paise"] == 0


def test_mule_risk_payload_carries_no_raw_identifiers(analytics_client, token_for, tid):
    """The whole point of this endpoint is that it never needs a reveal gate - assert
    that by construction: every leaf value in the payload is numeric or the account the
    caller already supplied, never a device id, IP, or counterparty account number."""
    h = token_for("investigator")
    account = _a_real_account(analytics_client, tid, h)
    body = analytics_client.get(f"/analytics/{tid}/mule-risk/{account}", headers=h).json()

    def leaves(obj):
        if isinstance(obj, dict):
            for v in obj.values():
                yield from leaves(v)
        else:
            yield obj

    for value in leaves(body):
        assert isinstance(value, (int, float, str)), value
        if isinstance(value, str):
            assert value == account, f"unexpected string leaf in payload: {value!r}"
