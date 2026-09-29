"""Case evidence, model governance and scheduled distribution."""
import io

import pytest


# ---------------- case documents ----------------
def _upload(client, tid, headers, case_id, doc_type="reasoned_order",
            content=b"committee order text", filename="order.txt"):
    return client.post(
        f"/analytics/{tid}/cases/{case_id}/documents", headers=headers,
        data={"doc_type": doc_type, "note": "test"},
        files={"file": (filename, io.BytesIO(content), "text/plain")})


@pytest.fixture
def a_fraud_case(analytics_client, token_for, tid):
    rows = analytics_client.get(
        f"/analytics/{tid}/drill/case", headers=token_for("investigator"),
        params={"states": "fraud_declared", "limit": 1}).json()["rows"]
    if not rows:
        pytest.skip("no fraud-classified case in the fixture data")
    return rows[0]["case_id"]


def test_upload_and_download_round_trip(analytics_client, token_for, tid, a_fraud_case):
    h = token_for("investigator")
    body = b"Reasoned order: classified after considering the response."
    r = _upload(analytics_client, tid, h, a_fraud_case, content=body)
    assert r.status_code == 201, r.text
    doc = r.json()
    assert doc["size_bytes"] == len(body)

    got = analytics_client.get(f"/analytics/{tid}/documents/{doc['id']}", headers=h)
    assert got.status_code == 200
    assert got.content == body
    assert got.headers["x-content-sha256"] == doc["sha256"], "integrity digest mismatch"


def test_documents_have_no_delete_endpoint(analytics_client, token_for, tid, a_fraud_case):
    """A compliance trail that can be quietly pruned is not a trail."""
    h = token_for("tenant_admin")
    doc = _upload(analytics_client, tid, h, a_fraud_case).json()
    r = analytics_client.delete(f"/analytics/{tid}/documents/{doc['id']}", headers=h)
    assert r.status_code in (404, 405), "documents must not be deletable"


def test_bad_doc_type_and_empty_file_are_rejected(analytics_client, token_for, tid,
                                                  a_fraud_case):
    h = token_for("investigator")
    assert _upload(analytics_client, tid, h, a_fraud_case,
                   doc_type="not_a_type").status_code == 400
    assert _upload(analytics_client, tid, h, a_fraud_case,
                   content=b"").status_code == 400


def test_attaching_a_reasoned_order_closes_the_compliance_gap(
        analytics_client, token_for, tid):
    """The metric that makes documents matter: fraud classified with no reasoned order."""
    h = token_for("supervisor")
    metric = lambda: analytics_client.get(  # noqa: E731
        f"/analytics/{tid}/rfa", headers=h).json()["metrics"][
        "fraud_without_reasoned_order"]["value"]

    rows = analytics_client.get(f"/analytics/{tid}/drill/case", headers=h,
                                params={"states": "fraud_declared", "limit": 50}).json()["rows"]
    # Earlier tests in this module attach orders to the first case, so pick one that does
    # not already have a reasoned order - otherwise the count cannot move.
    target = None
    for row in rows:
        docs = analytics_client.get(
            f"/analytics/{tid}/cases/{row['case_id']}/documents", headers=h).json()
        if not any(d["doc_type"] == "reasoned_order" for d in docs):
            target = row["case_id"]
            break
    if target is None:
        pytest.skip("every fraud case already has a reasoned order")

    before = metric()
    assert before > 0, "expected an open compliance gap to close"
    _upload(analytics_client, tid, h, target, doc_type="reasoned_order")
    assert metric() == before - 1, "attaching a reasoned order did not close the gap"


def test_document_access_is_audited(analytics_client, token_for, tid, a_fraud_case):
    from sqlalchemy import select
    from cp_common import AuditLog, SessionLocal
    h = token_for("investigator")
    doc = _upload(analytics_client, tid, h, a_fraud_case).json()
    analytics_client.get(f"/analytics/{tid}/documents/{doc['id']}", headers=h)
    db = SessionLocal()
    try:
        actions = {a.action for a in db.scalars(
            select(AuditLog).where(AuditLog.tenant_id == tid))}
    finally:
        db.close()
    assert "case.document_upload" in actions
    assert "case.document_view" in actions, "retrieving evidence was not audited"


# ---------------- model inventory & overrides ----------------
def test_model_inventory_is_derived_from_what_ran(analytics_client, token_for, tid):
    """A maintained spreadsheet drifts from production; the drift is the risk."""
    d = analytics_client.get(f"/analytics/{tid}/model-inventory",
                             headers=token_for("data_scientist")).json()
    assert d["in_production"] >= 1
    for m in d["models"]:
        assert m["config_version"]
        assert m["alerts"] > 0, "a version with no alerts is not in production"
    assert isinstance(d["governance_gap"], bool)


def test_unapproved_versions_are_flagged(analytics_client, token_for, tid):
    from services.analytics_service.app import governance as gov
    from services.analytics_service.app.engine.postgres import PostgresEngine
    from cp_common import SessionLocal
    db = SessionLocal()
    try:
        out = gov.inventory(PostgresEngine(db), tid, approved_versions={"9.9.9"})
    finally:
        db.close()
    assert out["governance_gap"] is True
    assert out["unapproved_in_production"], "a version scoring traffic was not flagged"


def test_override_log_reports_both_directions(analytics_client, token_for, tid):
    d = analytics_client.get(f"/analytics/{tid}/overrides",
                             headers=token_for("data_scientist")).json()
    assert d["dispositioned"] > 0
    assert d["overrides"] == d["dismissed_strong"] + d["escalated_weak"]
    assert 0 <= (d["override_rate"] or 0) <= 1
    for item in d["recent"]:
        if item["kind"] == "dismissed_strong_signal":
            assert item["score"] >= d["thresholds"]["strong_signal"]
        else:
            assert item["score"] <= d["thresholds"]["weak_signal"]


def test_governance_is_restricted_to_model_risk_roles(analytics_client, token_for, tid):
    for path in ("model-inventory", "overrides"):
        assert analytics_client.get(f"/analytics/{tid}/{path}",
                                    headers=token_for("analyst")).status_code == 403


# ---------------- scheduled distribution ----------------
def test_subscription_round_trip_and_ownership(analytics_client, token_for, tid):
    owner, other = token_for("supervisor"), token_for("analyst")
    r = analytics_client.post(f"/analytics/{tid}/subscriptions", headers=owner, json={
        "name": "Board pack", "entity": "case", "query": "rfa_only=true",
        "cadence": "weekly", "driver": "spool"})
    assert r.status_code == 201, r.text
    sub = r.json()
    assert sub["due_now"] is True, "a never-run subscription should be due"

    mine = analytics_client.get(f"/analytics/{tid}/subscriptions", headers=other).json()
    assert not any(s["id"] == sub["id"] for s in mine), "another user's subscription leaked"

    assert analytics_client.delete(f"/analytics/{tid}/subscriptions/{sub['id']}",
                                   headers=other).status_code in (403, 404)
    assert analytics_client.delete(f"/analytics/{tid}/subscriptions/{sub['id']}",
                                   headers=owner).status_code == 204


def test_cadence_controls_whether_a_subscription_is_due():
    from datetime import datetime, timedelta, timezone
    from services.analytics_service.app.subscriptions_model import ReportSubscription

    now = datetime.now(timezone.utc)
    sub = ReportSubscription(tenant_id="t", owner="o", name="n", cadence="daily",
                             active=True)
    assert sub.is_due(now), "never run means due"
    sub.last_run_at = now - timedelta(hours=2)
    assert not sub.is_due(now)
    sub.last_run_at = now - timedelta(days=2)
    assert sub.is_due(now)
    sub.active = False
    assert not sub.is_due(now), "an inactive subscription must never run"


def test_email_driver_does_not_claim_to_have_sent(tmp_path, monkeypatch):
    """A governance control that silently fails is worse than one visibly absent."""
    from scripts import run_subscriptions as runner
    monkeypatch.setattr(runner, "SPOOL", tmp_path)
    status = runner.deliver("email", "board@bank.example", "r.csv", "a,b\n1,2\n")
    assert "NOT SENT" in status
    assert (tmp_path / "r.csv").exists(), "artefact must still be retained"


def test_subscription_filters_are_rebuilt_from_the_stored_query():
    from scripts.run_subscriptions import filters_from_query
    f = filters_from_query("tenant-1", "rails=UPI&rails=IMPS&rfa_only=true&fmr_status=overdue")
    assert f.rails == ["UPI", "IMPS"]
    assert f.rfa_only is True
    assert f.fmr_status == "overdue"
    assert f.tenant_id == "tenant-1"
