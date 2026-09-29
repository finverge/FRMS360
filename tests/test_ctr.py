"""BR-508: Cash Transaction Report aggregation and filing.

PMLA Rule 3 requires reporting once an account's cash transactions for a calendar month
total Rs 10,00,000, deposits and withdrawals both, whether that arrives as one
transaction or several smaller ones. Cash does not traverse UPI/NEFT/RTGS/CARD, so this
rides the CBS event intake (BR-211), extended with a new event kind rather than a new
ingestion pipeline - see cbs_models.py and ctr.py's own docstrings for why.
"""
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text

from cp_common.db import SessionLocal
from services.analytics_service.app import ctr
from services.analytics_service.app import data_source
from services.ingestion_service.app.adapters import AdapterError
from services.ingestion_service.app.cbs_adapter import adapt_cbs

ACC = "AC-CTR-001"


@pytest.fixture(autouse=True)
def _clean(tid):
    yield
    db = SessionLocal()
    try:
        db.execute(text("DELETE FROM ingestion.cbs_events WHERE tenant_id = :t "
                        "AND kind = 'cash_transaction'"), {"t": tid})
        db.execute(text("DELETE FROM analytics.ctr_filings WHERE tenant_id = :t"),
                   {"t": tid})
        db.commit()
    finally:
        db.close()


def _period_now() -> str:
    return f"{datetime.now(timezone.utc):%Y-%m}"


def _ev(account, amount, direction, n, *, source="live", ts=None):
    now = datetime.now(timezone.utc)
    return {
        "event_id": f"E-CTR-{account}-{n}", "kind": "cash_transaction",
        "ts": (ts or (now - timedelta(days=1))).isoformat(),
        "account": account, "amount": amount, "direction": direction,
    }


def _post(client, token_for, tid, payloads, role="tenant_admin", source="live"):
    return client.post(f"/ingest/{tid}/cbs-events", headers=token_for(role),
                       json={"source": source,
                             "events": [{"payload": p} for p in payloads]})


# ------------------------------------------------------------------ the adapter
def test_cash_transaction_requires_a_valid_direction():
    with pytest.raises(AdapterError) as exc:
        adapt_cbs({"event_id": "1", "kind": "cash_transaction",
                  "ts": "2026-08-01T00:00:00Z", "account": ACC, "amount": 50000})
    assert "direction" in str(exc.value)


def test_cash_transaction_rejects_an_unrecognised_direction():
    with pytest.raises(AdapterError):
        adapt_cbs({"event_id": "1", "kind": "cash_transaction",
                  "ts": "2026-08-01T00:00:00Z", "account": ACC, "amount": 50000,
                  "direction": "sideways"})


def test_cash_transaction_is_accepted_with_deposit_or_withdrawal():
    for d in ("deposit", "withdrawal"):
        out = adapt_cbs({"event_id": f"1-{d}", "kind": "cash_transaction",
                         "ts": "2026-08-01T00:00:00Z", "account": ACC,
                         "amount": 50000, "direction": d})
        assert out["attributes"]["direction"] == d


def test_cash_withdrawal_kind_is_unaffected_and_needs_no_direction():
    """The pre-existing CBS-02 kind stays exactly as it was."""
    out = adapt_cbs({"event_id": "1", "kind": "cash_withdrawal",
                     "ts": "2026-08-01T00:00:00Z", "account": ACC, "amount": 50000})
    assert out["kind"] == "cash_withdrawal"


# --------------------------------------------------------------- month bounds
def test_month_bounds_is_a_half_open_calendar_month():
    start, end = ctr.month_bounds("2026-02")
    assert start == datetime(2026, 2, 1, tzinfo=timezone.utc)
    assert end == datetime(2026, 3, 1, tzinfo=timezone.utc)


def test_month_bounds_wraps_december_into_the_next_year():
    start, end = ctr.month_bounds("2026-12")
    assert start == datetime(2026, 12, 1, tzinfo=timezone.utc)
    assert end == datetime(2027, 1, 1, tzinfo=timezone.utc)


@pytest.mark.parametrize("bad", ["2026", "2026-13", "not-a-period", "2026-00"])
def test_month_bounds_refuses_a_malformed_period(bad):
    with pytest.raises(ValueError):
        ctr.month_bounds(bad)


# ------------------------------------------------------------- aggregation
def test_deposits_and_withdrawals_both_count_toward_the_same_total(
        ingestion_client, token_for, tid):
    r = _post(ingestion_client, token_for, tid, [
        _ev(ACC, "600000", "deposit", 1),
        _ev(ACC, "500000", "withdrawal", 2),
    ])
    assert r.status_code == 202, r.text
    assert r.json()["accepted"] == 2

    db = SessionLocal()
    try:
        rows = ctr.aggregate_period(db, tid, _period_now())
    finally:
        db.close()
    row = next(r for r in rows if r["account"] == ACC)
    assert int(row["total_paise"]) == 600000_00 + 500000_00
    assert int(row["deposit_paise"]) == 600000_00
    assert int(row["withdrawal_paise"]) == 500000_00
    assert int(row["txn_count"]) == 2


def test_several_small_transactions_aggregate_past_the_threshold(
        ingestion_client, token_for, tid):
    """PMLA Rule 3: connected transactions below the threshold individually still
    trigger the obligation once they add up within the month."""
    r = _post(ingestion_client, token_for, tid, [
        _ev(ACC, "300000", "deposit", i) for i in range(4)
    ])
    assert r.status_code == 202, r.text

    db = SessionLocal()
    try:
        due = ctr.due_accounts(db, tid, _period_now())
    finally:
        db.close()
    assert any(r["account"] == ACC for r in due), \
        "4 x Rs 3,00,000 = Rs 12,00,000 should meet the Rs 10,00,000 threshold"


def test_an_account_below_threshold_is_not_due(ingestion_client, token_for, tid):
    r = _post(ingestion_client, token_for, tid, [_ev(ACC, "50000", "deposit", 1)])
    assert r.status_code == 202, r.text

    db = SessionLocal()
    try:
        due = ctr.due_accounts(db, tid, _period_now())
    finally:
        db.close()
    assert not any(r["account"] == ACC for r in due)


def test_transactions_outside_the_period_do_not_count(ingestion_client, token_for, tid):
    last_month = datetime.now(timezone.utc).replace(day=1) - timedelta(days=1)
    r = _post(ingestion_client, token_for, tid, [
        _ev(ACC, "1500000", "deposit", 1, ts=last_month)
    ])
    assert r.status_code == 202, r.text

    db = SessionLocal()
    try:
        due = ctr.due_accounts(db, tid, _period_now())
    finally:
        db.close()
    assert not any(r["account"] == ACC for r in due), \
        "a transaction from a different calendar month must not count toward this one"


# ------------------------------------------------------------ build() / provenance
def test_build_refuses_an_account_with_no_cash_transactions(tid):
    db = SessionLocal()
    try:
        with pytest.raises(LookupError):
            ctr.build(db, tenant_id=tid, account="AC-NEVER-SEEN", period=_period_now(),
                     entity={})
    finally:
        db.close()


def test_build_refuses_data_that_is_not_marked_live(ingestion_client, token_for, tid):
    r = _post(ingestion_client, token_for, tid,
             [_ev(ACC, "1200000", "deposit", 1)], source="synthetic")
    assert r.status_code == 202, r.text

    db = SessionLocal()
    try:
        with pytest.raises(data_source.NotLiveData):
            ctr.build(db, tenant_id=tid, account=ACC, period=_period_now(), entity={})
    finally:
        db.close()


def test_build_succeeds_from_explicitly_live_data(ingestion_client, token_for, tid):
    r = _post(ingestion_client, token_for, tid,
             [_ev(ACC, "1200000", "deposit", 1)], source="live")
    assert r.status_code == 202, r.text

    db = SessionLocal()
    try:
        payload = ctr.build(db, tenant_id=tid, account=ACC, period=_period_now(),
                            entity={"legal_name": "Test Bank"})
    finally:
        db.close()
    assert payload["threshold_met"] is True
    assert payload["total_amount_paise"] == 1200000_00
    assert payload["transaction_count"] == 1
    assert payload["account"] == ACC


def test_validate_reports_the_threshold_as_a_blocking_gap_when_unmet(tid,
                                                                     ingestion_client,
                                                                     token_for):
    _post(ingestion_client, token_for, tid, [_ev(ACC, "10000", "deposit", 1)])
    db = SessionLocal()
    try:
        payload = ctr.build(db, tenant_id=tid, account=ACC, period=_period_now(),
                            entity={"legal_name": "Test Bank"})
    finally:
        db.close()
    v = ctr.validate(payload)
    assert not v.ready
    assert any(g.key == "threshold_met" for g in v.blocking)


# --------------------------------------------------------------------- routes
def test_due_endpoint_lists_accounts_meeting_the_threshold(
        analytics_client, ingestion_client, token_for, tid):
    _post(ingestion_client, token_for, tid, [_ev(ACC, "1500000", "deposit", 1)])
    r = analytics_client.get(f"/analytics/{tid}/ctr/due",
                             headers=token_for("supervisor"),
                             params={"period": _period_now()})
    assert r.status_code == 200, r.text
    assert any(i["account"] == ACC for i in r.json()["items"])


def test_readiness_before_generation_shows_the_gap(analytics_client, ingestion_client,
                                                    token_for, tid):
    _post(ingestion_client, token_for, tid, [_ev(ACC, "5000", "deposit", 1)])
    r = analytics_client.get(f"/analytics/{tid}/ctr/{ACC}/{_period_now()}/readiness",
                             headers=token_for("supervisor"))
    assert r.status_code == 200, r.text
    assert r.json()["ready"] is False


def test_generate_refuses_below_threshold(analytics_client, ingestion_client,
                                          token_for, tid):
    _post(ingestion_client, token_for, tid, [_ev(ACC, "5000", "deposit", 1)])
    r = analytics_client.post(f"/analytics/{tid}/ctr/{ACC}/{_period_now()}/generate",
                              headers=token_for("principal_officer"))
    assert r.status_code == 409, r.text
    assert r.json()["error"]["code"] == "not_ready_to_file"


def test_an_analyst_may_not_generate_a_ctr(analytics_client, ingestion_client,
                                           token_for, tid):
    _post(ingestion_client, token_for, tid, [_ev(ACC, "1500000", "deposit", 1)])
    r = analytics_client.post(f"/analytics/{tid}/ctr/{ACC}/{_period_now()}/generate",
                              headers=token_for("analyst"))
    assert r.status_code == 403, r.text


def test_generate_download_and_acknowledge_end_to_end(analytics_client,
                                                       ingestion_client, token_for, tid):
    _post(ingestion_client, token_for, tid, [_ev(ACC, "1500000", "deposit", 1)])
    period = _period_now()
    h = token_for("principal_officer")

    made = analytics_client.post(f"/analytics/{tid}/ctr/{ACC}/{period}/generate",
                                 headers=h)
    assert made.status_code == 200, made.text
    body = made.json()
    assert body["validation"]["ready"] is True
    filing_id = body["id"]

    listed = analytics_client.get(f"/analytics/{tid}/ctr/{ACC}/{period}", headers=h)
    assert listed.status_code == 200
    assert any(f["id"] == filing_id for f in listed.json())

    html = analytics_client.get(f"/analytics/{tid}/ctr-filings/{filing_id}/download",
                                headers=h, params={"fmt": "html"})
    assert html.status_code == 200
    assert ACC in html.text

    js = analytics_client.get(f"/analytics/{tid}/ctr-filings/{filing_id}/download",
                              headers=h, params={"fmt": "json"})
    assert js.status_code == 200
    assert js.json()["return"]["account"] == ACC

    ack = analytics_client.post(
        f"/analytics/{tid}/ctr-filings/{filing_id}/acknowledge", headers=h,
        json={"reference_number": "FIU-CTR-2026-0042"})
    assert ack.status_code == 200, ack.text
    assert ack.json()["status"] == "acknowledged"

    again = analytics_client.post(
        f"/analytics/{tid}/ctr-filings/{filing_id}/acknowledge", headers=h,
        json={"reference_number": "FIU-CTR-2026-0099"})
    assert again.status_code == 409
    assert again.json()["error"]["code"] == "already_submitted"


def test_a_regenerated_ctr_supersedes_the_prior_unacknowledged_one(
        analytics_client, ingestion_client, token_for, tid):
    _post(ingestion_client, token_for, tid, [_ev(ACC, "1500000", "deposit", 1)])
    period = _period_now()
    h = token_for("principal_officer")

    first = analytics_client.post(f"/analytics/{tid}/ctr/{ACC}/{period}/generate",
                                  headers=h).json()
    second = analytics_client.post(f"/analytics/{tid}/ctr/{ACC}/{period}/generate",
                                   headers=h).json()
    assert second["revision"] == first["revision"] + 1

    listed = analytics_client.get(f"/analytics/{tid}/ctr/{ACC}/{period}",
                                  headers=h).json()
    by_id = {f["id"]: f for f in listed}
    assert by_id[first["id"]]["status"] == "superseded"
    assert by_id[second["id"]]["status"] == "generated"
