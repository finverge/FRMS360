"""AI/ML roadmap Phase 2, end to end through the real detection worker (BRD OD-06/s19;
HLD AD-14/s16) - not the unit-level model_scoring.py tests, not the config-gate tests
in test_model_governance.py, but the two joined: an activated model version genuinely
scores VEL-04 in engine.run_once(), and a tenant with none active gets the honest
"unmeasurable" answer rather than a silent gap in the coverage panel.
"""
import hashlib
from datetime import datetime, timedelta, timezone

import joblib
import pytest
from sklearn.ensemble import IsolationForest
from sqlalchemy import text

from cp_common.db import SessionLocal
from services.analytics_service.app.detection import run_until_empty
from services.analytics_service.app.rules import active_model

HUB = "AC-MDL-HUB-1"

# A toy model that has only ever seen accounts with a handful of counterparties - a
# fan-in hub with 46 is then a genuine, learned outlier to it, not a hand-picked score.
_NORMAL = [[0.2 + 0.01 * i, 3.0, 0.2, 0.0, 0.0] for i in range(40)]


@pytest.fixture
def clean_slate(tid):
    yield
    db = SessionLocal()
    try:
        db.execute(text(
            "DELETE FROM analytics.fact_alert WHERE tenant_id = :t AND txn_id LIKE 'MDL-%'"),
            {"t": tid})
        db.execute(text(
            "DELETE FROM analytics.fact_transaction "
            "WHERE tenant_id = :t AND txn_id LIKE 'MDL-%'"), {"t": tid})
        db.execute(text(
            "DELETE FROM ingestion.raw_transaction "
            "WHERE tenant_id = :t AND source_txn_id LIKE 'MDL-%'"), {"t": tid})
        # The fan-in also opens a case (LAY-02); deleting its alerts above without also
        # deleting the now-orphaned case leaves it behind for a later test's
        # reconciliation checks to trip over - the exact same guard
        # test_detection.py's own clean_slate fixture carries.
        db.execute(text(
            "DELETE FROM analytics.fact_case WHERE tenant_id = :t AND case_id IN ("
            "  SELECT case_id FROM analytics.fact_case c WHERE c.tenant_id = :t"
            "    AND NOT EXISTS (SELECT 1 FROM analytics.fact_alert a "
            "                    WHERE a.case_id = c.case_id))"), {"t": tid})
        db.commit()
    finally:
        db.close()


@pytest.fixture
def active_model_version(config_client, token_for, tid, tmp_path):
    """Trains a tiny real model, activates it through the actual maker-checker flow
    (BR-807/808/AD-14), and tears the config back out afterwards so it cannot leak into
    a later test's traffic."""
    from services.analytics_service.app import rules as bridge

    estimator = IsolationForest(n_estimators=50, random_state=42).fit(_NORMAL)
    raw = estimator.score_samples(_NORMAL)
    bundle = {"estimator": estimator, "score_low": float(min(raw)),
             "score_high": float(max(raw))}
    path = tmp_path / "toy_model.joblib"
    joblib.dump(bundle, path)
    digest = hashlib.sha256(path.read_bytes()).hexdigest()

    made = config_client.post(
        f"/configs/{tid}", headers=token_for("tenant_admin"),
        # Must be "velocity-anomaly" - engine.py looks up VEL-04's model by exactly
        # this name (features.NEEDS_MODEL["VEL-04"]), not by "whichever model this
        # tenant has active", now that a tenant can have more than one.
        json={"kind": "model", "name": "velocity-anomaly", "version": "1.0.0",
              "body": {
                  "attestation": {"approved_by": "model_risk_committee",
                                  "approved_on": "2026-08-01",
                                  "reference": "MRC minute — integration test"},
                  "challenger_evaluation": {
                      "evaluated_by": "Integration test", "evaluated_on": "2026-08-01",
                      "method": "Fit against a synthetic separable toy set",
                      "metrics": {"precision": 1.0},
                      "comparison_summary": "First version; no champion to compare.",
                  },
                  "model_artifact": {"format": "sklearn-joblib",
                                    "uri": path.resolve().as_uri(), "sha256": digest},
              }})
    assert made.status_code == 201, made.text
    config_id = made.json()["id"]
    proposed = config_client.post(f"/configs/{tid}/{config_id}/activate",
                                  headers=token_for("tenant_admin"))
    assert proposed.status_code == 200, proposed.text
    confirmed = config_client.post(f"/configs/{tid}/{config_id}/activate",
                                   headers=token_for("risk_manager"))
    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json()["status"] == "active"

    bridge.invalidate(tid)  # the versioned cache must see this generation immediately
    yield

    db = SessionLocal()
    try:
        db.execute(text(
            "DELETE FROM config.tenant_configs WHERE tenant_id = :t AND kind = 'model'"),
            {"t": tid})
        db.commit()
    finally:
        db.close()
    bridge.invalidate(tid)


def _fan_in(tid, ingestion_client, token_for, tag: str, payers: int = 46) -> int:
    now = datetime.now(timezone.utc)
    txns = [{"rail": "UPI", "payload": {
        "upiTransactionId": f"MDL-{tag}-{i:04d}",
        "txnTimestamp": (now - timedelta(hours=5, minutes=i)).isoformat(),
        "amountRupees": "24000.00",
        "payerAccount": f"AC-MDL-P{i:03d}", "payeeAccount": HUB,
        "region": "west", "device_id": f"DEV-{i}"}} for i in range(payers)]
    r = ingestion_client.post(f"/ingest/{tid}/transactions",
                              headers=token_for("tenant_admin"),
                              json={"transactions": txns, "source": "test"})
    assert r.status_code == 202, r.text
    return r.json()["accepted"]


def _run(tid):
    db = SessionLocal()
    try:
        return run_until_empty(db, tid, batch=500)
    finally:
        db.close()


def test_vel04_is_unmeasurable_with_no_active_model(ingestion_client, token_for, tid,
                                                     clean_slate):
    _fan_in(tid, ingestion_client, token_for, "NOMODEL")
    rep = _run(tid)
    assert "VEL-04" in rep.unmeasurable
    assert "no active model" in rep.unmeasurable["VEL-04"].lower()
    assert "VEL-04" not in rep.fired_rules


def test_an_active_model_genuinely_scores_traffic(ingestion_client, token_for, tid,
                                                   active_model_version, clean_slate):
    """The whole point: not a stub, not a mock - a real fitted IsolationForest, loaded
    from a config-service-recorded artifact, scoring a real batch through the same
    engine.run_once() every other rule scores through."""
    _fan_in(tid, ingestion_client, token_for, "SCORED")
    rep = _run(tid)
    assert "VEL-04" not in rep.unmeasurable
    assert rep.fired_rules.get("VEL-04", 0) >= 1, (
        "a 46-payer fan-in should read as anomalous to a model trained only on "
        "accounts with a handful of counterparties")

    db = SessionLocal()
    try:
        alert = db.execute(text(
            "SELECT observed_value, threshold_value, sub_rule_ref FROM analytics.fact_alert "
            "WHERE tenant_id = :t AND rule_id = 'VEL-04' LIMIT 1"), {"t": tid}).mappings().first()
    finally:
        db.close()
    assert alert is not None
    assert 0.0 <= alert["observed_value"] <= 1.0
    assert alert["threshold_value"] == pytest.approx(0.7)


def test_active_model_endpoint_agrees_with_the_console_flow(tid, active_model_version):
    """analytics-service's own view of the tenant's active model, not just
    config-service's - the same bridge engine.py actually calls."""
    info = active_model(tid, "velocity-anomaly", force=True)
    assert info["available"] is True
    assert info["body"]["model_artifact"]["format"] == "sklearn-joblib"
