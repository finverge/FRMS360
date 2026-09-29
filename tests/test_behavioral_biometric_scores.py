"""Device-posture / behavioural-biometric scores (CHN-04/CHN-05), end to end.

adapters.py has accepted device_risk_score/behavior_anomaly_score on any rail payload
since the AI/ML roadmap Phase 1 work, and observe() already turns them into CHN-04/
CHN-05 - but detection/engine.py's projection step never carried either field onto
fact_transaction, so a score that arrived, scored, and fired an alert was gone from the
record afterward. This asserts the whole path now holds together: ingest -> persisted on
fact_transaction -> alert fires -> visible on the transaction's own evidence payload.
"""
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text

from cp_common.db import SessionLocal
from services.analytics_service.app.detection import run_until_empty


@pytest.fixture
def clean_biometric_slate(tid):
    yield
    db = SessionLocal()
    try:
        db.execute(text(
            "DELETE FROM analytics.fact_alert WHERE tenant_id = :t AND txn_id LIKE 'BIO-%'"),
            {"t": tid})
        db.execute(text(
            "DELETE FROM analytics.fact_transaction WHERE tenant_id = :t AND txn_id LIKE 'BIO-%'"),
            {"t": tid})
        db.execute(text(
            "DELETE FROM analytics.fact_case WHERE tenant_id = :t AND case_id IN ("
            "  SELECT case_id FROM analytics.fact_case c WHERE c.tenant_id = :t"
            "    AND NOT EXISTS (SELECT 1 FROM analytics.fact_alert a "
            "                    WHERE a.case_id = c.case_id))"), {"t": tid})
        db.execute(text(
            "DELETE FROM ingestion.raw_transaction WHERE tenant_id = :t AND source_txn_id LIKE 'BIO-%'"),
            {"t": tid})
        db.commit()
    finally:
        db.close()


def _ingest_one(tid, ingestion_client, token_for, tag: str, **scores) -> None:
    now = datetime.now(timezone.utc)
    payload = {
        "upiTransactionId": f"BIO-{tag}",
        "txnTimestamp": (now - timedelta(minutes=5)).isoformat(),
        "amountRupees": "5000.00",
        "payerAccount": "AC-BIO-PAYER", "payeeAccount": "AC-BIO-PAYEE",
        "region": "west", "device_id": "DEV-BIO",
        **scores,
    }
    r = ingestion_client.post(
        f"/ingest/{tid}/transactions", headers=token_for("tenant_admin"),
        json={"transactions": [{"rail": "UPI", "payload": payload}], "source": "test"})
    assert r.status_code == 202, r.text
    assert r.json()["accepted"] == 1, r.text


def _run(tid):
    db = SessionLocal()
    try:
        return run_until_empty(db, tid, batch=500)
    finally:
        db.close()


def test_biometric_scores_persist_and_fire_end_to_end(
    ingestion_client, analytics_client, token_for, tid, clean_biometric_slate,
):
    _ingest_one(tid, ingestion_client, token_for, "HIGH",
                device_risk_score=0.91, behavior_anomaly_score=0.85)
    rep = _run(tid)
    assert "CHN-04" in rep.fired_rules, rep.fired_rules
    assert "CHN-05" in rep.fired_rules, rep.fired_rules

    h = token_for("investigator")
    r = analytics_client.get(f"/analytics/{tid}/evidence/transaction/BIO-HIGH", headers=h)
    assert r.status_code == 200, r.text
    txn = r.json()["transaction"]
    assert txn["device_risk_score"] == pytest.approx(0.91)
    assert txn["behavior_anomaly_score"] == pytest.approx(0.85)

    alerts = r.json()["alerts"]
    fired_rule_ids = {a["rule_id"] for a in alerts}
    assert {"CHN-04", "CHN-05"} <= fired_rule_ids


def test_biometric_scores_absent_when_no_provider_supplies_them(
    ingestion_client, analytics_client, token_for, tid, clean_biometric_slate,
):
    """The normal case: no integrated provider, so no score - and CHN-04/CHN-05 must
    not fire on an absent score the way they would on a score of 0.0."""
    _ingest_one(tid, ingestion_client, token_for, "NONE")
    rep = _run(tid)
    assert "CHN-04" not in rep.fired_rules
    assert "CHN-05" not in rep.fired_rules

    h = token_for("investigator")
    r = analytics_client.get(f"/analytics/{tid}/evidence/transaction/BIO-NONE", headers=h)
    assert r.status_code == 200, r.text
    txn = r.json()["transaction"]
    assert txn["device_risk_score"] is None
    assert txn["behavior_anomaly_score"] is None
