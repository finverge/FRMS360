"""AI/ML roadmap Phase 3, end to end (BRD OD-06/s19; HLD AD-15/s16).

Two things are tested separately, deliberately:

1. **refresh_ring_scores.refresh_one()'s own plumbing** - a real (if tiny) GraphSAGE +
   IsolationForest trained against real projected transactions, activated through the
   actual maker-checker flow, refreshed, and the resulting rows checked for shape and
   range. Not whether specific accounts get flagged - a first-working-fit GNN's
   separability on a small graph is exactly the kind of thing that would make an
   assertion here flaky without meaning anything about a real bug.

2. **engine.py's LAY-05 lookup** - deterministic, because the scores it reads are
   inserted directly rather than depending on what a freshly-trained model happens to
   produce. This is the actually bug-prone code (a lookup, a threshold comparison, an
   honest unmeasurable path) and it deserves a test that fails only when it is wrong.
"""
import hashlib
from datetime import datetime, timedelta, timezone

import joblib
import pytest
from sklearn.ensemble import IsolationForest
from sqlalchemy import text

from cp_common.db import SessionLocal
from services.analytics_service.app.detection import graph_model as gm
from services.analytics_service.app.detection import run_until_empty
from services.analytics_service.app.detection.graph_model import RingScoreEncoder
from services.analytics_service.app.rules import active_model

HUB = "AC-RING-HUB-1"


@pytest.fixture
def clean_slate(tid):
    yield
    db = SessionLocal()
    try:
        db.execute(text(
            "DELETE FROM analytics.fact_alert WHERE tenant_id = :t AND txn_id LIKE 'RING-%'"),
            {"t": tid})
        db.execute(text(
            "DELETE FROM analytics.fact_transaction "
            "WHERE tenant_id = :t AND txn_id LIKE 'RING-%'"), {"t": tid})
        db.execute(text(
            "DELETE FROM ingestion.raw_transaction "
            "WHERE tenant_id = :t AND source_txn_id LIKE 'RING-%'"), {"t": tid})
        db.execute(text(
            "DELETE FROM analytics.account_ring_score WHERE tenant_id = :t"), {"t": tid})
        # Same orphaned-case guard test_detection.py's own clean_slate has - the fan-in
        # below opens a case (LAY-02) whose alerts get deleted above.
        db.execute(text(
            "DELETE FROM analytics.fact_case WHERE tenant_id = :t AND case_id IN ("
            "  SELECT case_id FROM analytics.fact_case c WHERE c.tenant_id = :t"
            "    AND NOT EXISTS (SELECT 1 FROM analytics.fact_alert a "
            "                    WHERE a.case_id = c.case_id))"), {"t": tid})
        db.commit()
    finally:
        db.close()


def _fan_in(tid, ingestion_client, token_for, tag: str, payers: int = 46) -> int:
    now = datetime.now(timezone.utc)
    txns = [{"rail": "UPI", "payload": {
        "upiTransactionId": f"RING-{tag}-{i:04d}",
        "txnTimestamp": (now - timedelta(hours=5, minutes=i)).isoformat(),
        "amountRupees": "24000.00",
        "payerAccount": f"AC-RING-P{i:03d}", "payeeAccount": HUB,
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


def _activate_graph_model(config_client, token_for, tid, artifact_path, digest,
                          *, name="graph-ring-score"):
    from services.analytics_service.app import rules as bridge

    made = config_client.post(
        f"/configs/{tid}", headers=token_for("tenant_admin"),
        json={"kind": "model", "name": name, "version": "1.0.0", "body": {
            "attestation": {"approved_by": "model_risk_committee",
                            "approved_on": "2026-08-01",
                            "reference": "MRC minute — ring score integration test"},
            "challenger_evaluation": {
                "evaluated_by": "Integration test", "evaluated_on": "2026-08-01",
                "method": "Fit against a real projected graph", "metrics": {"auc": 0.5},
                "comparison_summary": "First version; no champion to compare.",
            },
            "model_artifact": {"format": "graph-embedding-isolation-forest",
                              "uri": artifact_path.resolve().as_uri(), "sha256": digest},
        }})
    assert made.status_code == 201, made.text
    config_id = made.json()["id"]
    proposed = config_client.post(f"/configs/{tid}/{config_id}/activate",
                                  headers=token_for("tenant_admin"))
    assert proposed.status_code == 200, proposed.text
    confirmed = config_client.post(f"/configs/{tid}/{config_id}/activate",
                                   headers=token_for("risk_manager"))
    assert confirmed.status_code == 200, confirmed.text
    bridge.invalidate(tid)


def _cleanup_model(tid, name="graph-ring-score"):
    db = SessionLocal()
    try:
        db.execute(text(
            "DELETE FROM config.tenant_configs WHERE tenant_id = :t AND kind = 'model' "
            "AND name = :n"), {"t": tid, "n": name})
        db.commit()
    finally:
        db.close()
    from services.analytics_service.app import rules as bridge
    bridge.invalidate(tid)


# ============================================================ refresh_one plumbing
def test_refresh_one_skips_cleanly_with_no_active_model(tid):
    from scripts.refresh_ring_scores import refresh_one

    db = SessionLocal()
    try:
        report = refresh_one(db, tid)
    finally:
        db.close()
    assert report["status"] == "skipped"
    assert "no active" in report["reason"]


def test_refresh_one_writes_real_scores_and_upserts_on_rerun(
        ingestion_client, config_client, token_for, tid, tmp_path, clean_slate):
    from scripts.refresh_ring_scores import refresh_one

    _fan_in(tid, ingestion_client, token_for, "REFRESH")
    _run(tid)  # project the fan-in into fact_transaction before building the graph

    db = SessionLocal()
    try:
        graph = gm.build_tenant_graph(db, tid, datetime.now(timezone.utc), window_days=1)
        assert HUB in graph.accounts, "the fan-in must have projected into the graph"
        encoder = gm.train_encoder(graph, epochs=5)  # a handful - plumbing test, not a
                                                      # model-quality one
        embeddings = gm.embed(encoder, graph)
        X = [embeddings[a] for a in graph.accounts]
        estimator = IsolationForest(n_estimators=20, random_state=42).fit(X)
        raw = estimator.score_samples(X)
        bundle = {"estimator": estimator, "score_low": float(min(raw)),
                 "score_high": float(max(raw)), "gnn_state_dict": encoder.state_dict(),
                 "gnn_in_dim": len(gm.NODE_FEATURE_NAMES)}
        path = tmp_path / "ring.joblib"
        joblib.dump(bundle, path)
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
    finally:
        db.close()

    try:
        _activate_graph_model(config_client, token_for, tid, path, digest)

        db = SessionLocal()
        try:
            report = refresh_one(db, tid, window_days=1)
        finally:
            db.close()
        assert report["status"] == "refreshed"
        assert report["accounts_scored"] == len(graph.accounts)

        db = SessionLocal()
        try:
            rows = db.execute(text(
                "SELECT account, score FROM analytics.account_ring_score "
                "WHERE tenant_id = :t"), {"t": tid}).all()
        finally:
            db.close()
        by_account = dict(rows)
        assert HUB in by_account
        assert all(0.0 <= s <= 1.0 for _, s in rows)

        # Re-running must update, not duplicate - one row per account either way.
        db = SessionLocal()
        try:
            refresh_one(db, tid, window_days=1)
            recount = db.execute(text(
                "SELECT COUNT(*) FROM analytics.account_ring_score "
                "WHERE tenant_id = :t"), {"t": tid}).scalar()
        finally:
            db.close()
        assert recount == len(rows)
    finally:
        _cleanup_model(tid)


# ================================================================== engine's LAY-05
def test_lay05_is_unmeasurable_with_no_active_graph_model(
        ingestion_client, token_for, tid, clean_slate):
    _fan_in(tid, ingestion_client, token_for, "NOMODEL", payers=3)
    rep = _run(tid)
    assert "LAY-05" in rep.unmeasurable
    assert "no active" in rep.unmeasurable["LAY-05"].lower()
    assert "LAY-05" not in rep.fired_rules


def test_lay05_fires_from_a_directly_inserted_score(
        ingestion_client, config_client, token_for, tid, tmp_path, clean_slate):
    """Deterministic by construction: the score engine.py reads is inserted directly,
    not produced by a freshly-trained model whose separability this test has no
    business depending on."""
    encoder = RingScoreEncoder(in_dim=len(gm.NODE_FEATURE_NAMES))
    estimator = IsolationForest(n_estimators=10, random_state=0).fit(
        [[float(i)] * len(gm.NODE_FEATURE_NAMES) for i in range(10)])
    path = tmp_path / "ring.joblib"
    joblib.dump({"estimator": estimator, "score_low": 0.0, "score_high": 1.0,
                "gnn_state_dict": encoder.state_dict(),
                "gnn_in_dim": len(gm.NODE_FEATURE_NAMES)}, path)
    digest = hashlib.sha256(path.read_bytes()).hexdigest()

    try:
        _activate_graph_model(config_client, token_for, tid, path, digest)

        _fan_in(tid, ingestion_client, token_for, "FIRES", payers=3)
        db = SessionLocal()
        try:
            db.execute(text(
                "INSERT INTO analytics.account_ring_score "
                "(tenant_id, account, score, model_version, computed_at) "
                "VALUES (:t, :a, 0.95, '1.0.0', now())"), {"t": tid, "a": HUB})
            db.commit()
        finally:
            db.close()

        rep = _run(tid)
        assert "LAY-05" not in rep.unmeasurable, "an active model must not read as unmeasurable"
        assert rep.fired_rules.get("LAY-05", 0) >= 1

        db = SessionLocal()
        try:
            alert = db.execute(text(
                "SELECT observed_value, threshold_value FROM analytics.fact_alert "
                "WHERE tenant_id = :t AND rule_id = 'LAY-05' LIMIT 1"), {"t": tid}
                ).mappings().first()
        finally:
            db.close()
        assert alert is not None
        assert alert["observed_value"] == pytest.approx(0.95)
        assert alert["threshold_value"] == pytest.approx(0.7)
    finally:
        _cleanup_model(tid)


def test_lay05_stays_absent_for_an_account_with_no_computed_score_yet(
        ingestion_client, config_client, token_for, tid, tmp_path, clean_slate):
    """An active model but no refresh having scored this particular account is not the
    same as no model at all - it must simply not fire, not be reported as a tenant-wide
    coverage gap."""
    encoder = RingScoreEncoder(in_dim=len(gm.NODE_FEATURE_NAMES))
    estimator = IsolationForest(n_estimators=10, random_state=0).fit(
        [[float(i)] * len(gm.NODE_FEATURE_NAMES) for i in range(10)])
    path = tmp_path / "ring.joblib"
    joblib.dump({"estimator": estimator, "score_low": 0.0, "score_high": 1.0,
                "gnn_state_dict": encoder.state_dict(),
                "gnn_in_dim": len(gm.NODE_FEATURE_NAMES)}, path)
    digest = hashlib.sha256(path.read_bytes()).hexdigest()

    try:
        _activate_graph_model(config_client, token_for, tid, path, digest)
        _fan_in(tid, ingestion_client, token_for, "NOSCORE", payers=3)
        # No row inserted into account_ring_score for HUB - a refresh simply has never
        # run for it.
        rep = _run(tid)
        assert "LAY-05" not in rep.unmeasurable
        assert "LAY-05" not in rep.fired_rules
    finally:
        _cleanup_model(tid)
