"""Rail adapters and the ingestion queue.

The adapters are where a bank's format meets ours, so they are tested for the mistakes
that actually corrupt a fraud platform: money losing precision, timestamps losing their
timezone, and a replayed file double-counting value.
"""
import pytest

from services.ingestion_service.app.adapters import (
    RAILS, AdapterError, adapt, to_paise, to_utc,
)


# ------------------------------------------------------------------------- money
def test_money_never_goes_through_a_float():
    """Rs 1234.55 as a float is 123454.99999999999 paise.

    Over a day's volume that rounding is the difference between a reconciliation that
    ties and one that is out by an unexplainable few rupees.
    """
    assert to_paise("1234.55") == 123455
    assert to_paise("0.01") == 1
    assert to_paise(1000) == 100000
    assert to_paise("99999999.99") == 9999999999


def test_sub_paisa_amounts_are_refused_rather_than_rounded():
    """Silently rounding is how a ledger drifts. Refusing says which row was wrong."""
    with pytest.raises(AdapterError):
        to_paise("100.005")


@pytest.mark.parametrize("bad", ["", None, "abc", -5, True])
def test_nonsense_amounts_are_refused(bad):
    with pytest.raises(AdapterError):
        to_paise(bad)


# --------------------------------------------------------------------- timestamps
def test_a_naive_timestamp_is_refused():
    """IST is +05:30. Guessing wrong puts a transaction five and a half hours away from
    where it happened, which silently breaks every time-window rule in the catalogue."""
    with pytest.raises(AdapterError) as err:
        to_utc("2026-07-30 10:00:00")
    assert "timezone" in str(err.value).lower()


def test_offsets_are_normalised_to_utc():
    assert to_utc("2026-07-30T15:30:00+05:30").hour == 10
    assert to_utc("2026-07-30T10:00:00Z").hour == 10


# ----------------------------------------------------------------------- adapters
def test_every_rail_maps_to_the_same_canonical_shape():
    payloads = {
        "UPI": {"upiTransactionId": "U1", "txnTimestamp": "2026-07-30T10:00:00Z",
                "amountRupees": "1500.50", "payerAccount": "AC1", "payeeAccount": "AC2"},
        "IMPS": {"rrn": "R1", "txnDate": "2026-07-30T10:00:00Z", "amount": "1500.50",
                 "remitterAccount": "AC1", "beneficiaryAccount": "AC2"},
        "NEFT": {"utr": "N1", "instructionTime": "2026-07-30T10:00:00Z",
                 "amount": "1500.50", "senderAccount": "AC1", "beneficiaryAccount": "AC2"},
        "RTGS": {"utr": "T1", "settlementTime": "2026-07-30T10:00:00Z",
                 "amount": "1500.50", "senderAccount": "AC1", "beneficiaryAccount": "AC2"},
        "CARD": {"authCode": "C1", "authTime": "2026-07-30T10:00:00Z",
                 "authAmount": "1500.50", "cardAccount": "AC1", "merchantId": "AC2"},
    }
    assert set(payloads) == set(RAILS), "a rail exists with no adapter test"
    for rail, payload in payloads.items():
        out = adapt(rail, payload)
        assert out["rail"] == rail
        assert out["amount_paise"] == 150050, f"{rail} lost precision"
        assert out["debtor_account"] == "AC1" and out["creditor_account"] == "AC2"
        assert out["ts"].tzinfo is not None


def test_an_unsupported_rail_is_named_in_the_error():
    with pytest.raises(AdapterError) as err:
        adapt("SWIFT", {})
    assert "SWIFT" in str(err.value) and "UPI" in str(err.value)


# -------------------------------------- AI/ML roadmap Phase 1 (device/behavioural biometrics)
def test_biometric_scores_are_absent_by_default():
    """No rail sends these today - absent, not zero, which is what leaves CHN-04/CHN-05
    correctly unmeasured rather than looking like a risk-free transaction."""
    out = adapt("UPI", {"upiTransactionId": "U1", "txnTimestamp": "2026-07-30T10:00:00Z",
                        "amountRupees": "100", "payerAccount": "AC1", "payeeAccount": "AC2"})
    assert out["device_risk_score"] is None
    assert out["behavior_anomaly_score"] is None


def test_biometric_scores_pass_through_when_a_provider_supplies_them():
    out = adapt("UPI", {"upiTransactionId": "U1", "txnTimestamp": "2026-07-30T10:00:00Z",
                        "amountRupees": "100", "payerAccount": "AC1", "payeeAccount": "AC2",
                        "device_risk_score": "0.82", "behavior_anomaly_score": 0.15})
    assert out["device_risk_score"] == 0.82
    assert out["behavior_anomaly_score"] == 0.15


def test_behaviour_spelling_is_also_accepted():
    """A provider may send British spelling; both must land on the same canonical field."""
    out = adapt("UPI", {"upiTransactionId": "U1", "txnTimestamp": "2026-07-30T10:00:00Z",
                        "amountRupees": "100", "payerAccount": "AC1", "payeeAccount": "AC2",
                        "behaviour_anomaly_score": 0.3})
    assert out["behavior_anomaly_score"] == 0.3


@pytest.mark.parametrize("bad", [1.5, -0.1, "not-a-number"])
def test_out_of_range_biometric_scores_are_refused_not_clamped(bad):
    """A provider sending 1.5 has a bug worth surfacing, not a score to silently clip."""
    with pytest.raises(AdapterError):
        adapt("UPI", {"upiTransactionId": "U1", "txnTimestamp": "2026-07-30T10:00:00Z",
                      "amountRupees": "100", "payerAccount": "AC1", "payeeAccount": "AC2",
                      "device_risk_score": bad})


def test_a_self_transfer_is_refused():
    """Same account both sides is a mapping error, and it would make an account its own
    counterparty in every graph and layering rule."""
    with pytest.raises(AdapterError):
        adapt("UPI", {"upiTransactionId": "U1", "txnTimestamp": "2026-07-30T10:00:00Z",
                      "amountRupees": "10", "payerAccount": "AC1", "payeeAccount": "AC1"})


def test_missing_fields_say_which_ones():
    with pytest.raises(AdapterError) as err:
        adapt("UPI", {"txnTimestamp": "2026-07-30T10:00:00Z", "amountRupees": "10",
                      "payerAccount": "AC1", "payeeAccount": "AC2"})
    assert "txn_id" in str(err.value)


# ------------------------------------------------------------------------- the API
def _payload(txn_id: str, amount: str = "250000.00", **kw) -> dict:
    return {"rail": "UPI", "payload": {
        "upiTransactionId": txn_id, "txnTimestamp": "2026-07-30T10:00:00+05:30",
        "amountRupees": amount, "payerAccount": "AC-TEST-1",
        "payeeAccount": "AC-TEST-2", **kw}}


def test_a_batch_is_accepted_and_queued(ingestion_client, token_for, tid):
    r = ingestion_client.post(f"/ingest/{tid}/transactions",
                              headers=token_for("tenant_admin"),
                              json={"transactions": [_payload("ING-A1")],
                                    "source": "test"})
    assert r.status_code == 202, r.text
    assert r.json()["accepted"] == 1


def test_a_replayed_file_does_not_double_count(ingestion_client, token_for, tid):
    """Banks resend files. That must be boring, not an incident."""
    h = token_for("tenant_admin")
    body = {"transactions": [_payload("ING-DUP1")], "source": "test"}
    first = ingestion_client.post(f"/ingest/{tid}/transactions", headers=h, json=body)
    again = ingestion_client.post(f"/ingest/{tid}/transactions", headers=h, json=body)
    assert first.json()["accepted"] == 1
    assert again.json()["accepted"] == 0 and again.json()["duplicates"] == 1


def test_one_bad_row_does_not_reject_the_whole_batch(ingestion_client, token_for, tid):
    """Rejecting 5,000 good rows over one malformed timestamp is how a bank ends up
    building its own retry queue and sending everything twice."""
    bad = {"rail": "UPI", "payload": {"upiTransactionId": "ING-BAD1",
                                      "txnTimestamp": "2026-07-30 10:00:00",
                                      "amountRupees": "10", "payerAccount": "AC1",
                                      "payeeAccount": "AC2"}}
    r = ingestion_client.post(f"/ingest/{tid}/transactions",
                              headers=token_for("tenant_admin"),
                              json={"transactions": [_payload("ING-OK1"), bad],
                                    "source": "test"})
    body = r.json()
    assert body["accepted"] == 1 and body["rejected"] == 1
    assert body["errors"][0]["index"] == 1
    assert "timezone" in body["errors"][0]["error"].lower()


def test_ingestion_is_tenant_scoped(ingestion_client, token_for):
    r = ingestion_client.post("/ingest/another-bank/transactions",
                              headers=token_for("analyst"),
                              json={"transactions": [_payload("ING-X")]})
    assert r.status_code == 403


def test_status_reports_queue_depth_and_provenance(ingestion_client, token_for, tid):
    ingestion_client.post(f"/ingest/{tid}/transactions", headers=token_for("tenant_admin"),
                          json={"transactions": [_payload("ING-STAT1")], "source": "test"})
    body = ingestion_client.get(f"/ingest/{tid}/status",
                                headers=token_for("tenant_admin")).json()
    assert body["pending"] >= 1
    assert "test" in body["by_source"]
    assert set(body["supported_rails"]) == set(RAILS)


def test_claiming_requires_the_internal_key(ingestion_client, tid):
    r = ingestion_client.post(f"/internal/ingest/{tid}/claim", json={"limit": 1})
    assert r.status_code in (401, 403)
