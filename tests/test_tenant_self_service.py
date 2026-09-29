"""BR-109: self-service tenant onboarding and the isolated sandbox tenant.

Two properties matter here, and both are about what happens *without* a human from
Fraud360 in the loop:

* a prospective bank can provision a fully working tenant with no platform_admin
  action at all - that is the entire point of "self-service";
* what it gets is genuinely isolated - it can integrate against every real endpoint,
  but it can never mark anything it sends as regulatory-live traffic, however it is
  labelled at the call site, until a platform_admin makes the deliberate decision to
  promote it.
"""
from sqlalchemy import text

import pytest

from cp_common.db import SessionLocal
from cp_common.security import create_access_token


def _signup_payload(slug: str, email: str) -> dict:
    return {
        "slug": slug,
        "legal_name": f"{slug} Pvt Ltd",
        "display_name": slug,
        "entity_type": "commercial_bank",
        "admin_email": email,
        "admin_password": "SandboxPass#2026",
    }


@pytest.fixture(autouse=True)
def _clean(tenant_client, tid):
    """Sandbox tenants this file creates are cleaned up by slug prefix, so nothing
    here perturbs the shared seeded tenant or other tests' state. The one test that
    ingests against the shared tenant (proving a non-sandbox tenant is unaffected)
    cleans up its own row by its distinctive txn_id prefix, the same discipline
    test_detection.py's clean_slate fixture uses."""
    yield
    db = SessionLocal()
    try:
        db.execute(text(
            "DELETE FROM ingestion.raw_transaction WHERE tenant_id = :t "
            "AND source_txn_id LIKE 'BR109-%'"), {"t": tid})
        rows = db.execute(text(
            "SELECT id FROM tenant.tenants WHERE slug LIKE 'br109-%'")).scalars().all()
        for sandbox_tid in rows:
            db.execute(text(
                "DELETE FROM ingestion.raw_transaction WHERE tenant_id = :t"),
                {"t": sandbox_tid})
            db.execute(text("DELETE FROM tenant.tenant_users WHERE tenant_id = :t"),
                      {"t": sandbox_tid})
            db.execute(text("DELETE FROM tenant.tenant_roles WHERE tenant_id = :t"),
                      {"t": sandbox_tid})
            db.execute(text("DELETE FROM tenant.tenants WHERE id = :t"),
                      {"t": sandbox_tid})
        db.commit()
    finally:
        db.close()


# ------------------------------------------------------------------ signup itself
def test_self_service_signup_needs_no_bearer_token(tenant_client):
    r = tenant_client.post("/tenants/self-service",
                           json=_signup_payload("br109-noauth", "noauth@br109.example"))
    assert r.status_code == 201, r.text


def test_self_service_signup_produces_a_sandbox_tenant(tenant_client):
    r = tenant_client.post("/tenants/self-service",
                           json=_signup_payload("br109-sandbox1", "s1@br109.example"))
    body = r.json()
    assert body["is_sandbox"] is True
    assert body["plan"] == "sandbox"
    # The orchestration (branding, default configs) is the same one platform_admin's
    # POST /tenants already runs - self-service is a different door, not a shortcut
    # that skips provisioning.
    assert body["status"] in ("active", "degraded")


def test_the_admin_can_sign_in_with_the_password_they_chose(tenant_client):
    tenant_client.post("/tenants/self-service",
                       json=_signup_payload("br109-login", "login@br109.example"))
    r = tenant_client.post("/auth/login",
                           json={"email": "login@br109.example",
                                "password": "SandboxPass#2026"})
    assert r.status_code == 200, r.text


def test_a_slug_collision_is_refused_the_same_way_platform_admin_onboarding_refuses_it(tenant_client):
    tenant_client.post("/tenants/self-service",
                       json=_signup_payload("br109-dup", "first@br109.example"))
    r = tenant_client.post("/tenants/self-service",
                           json=_signup_payload("br109-dup", "second@br109.example"))
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "slug_conflict"


def test_a_second_signup_by_the_same_email_is_refused(tenant_client):
    """The abuse guard: one live self-service sandbox per admin email."""
    tenant_client.post("/tenants/self-service",
                       json=_signup_payload("br109-repeat1", "repeat@br109.example"))
    r = tenant_client.post("/tenants/self-service",
                           json=_signup_payload("br109-repeat2", "repeat@br109.example"))
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "sandbox_exists"


def test_a_different_email_is_never_blocked_by_someone_elses_sandbox(tenant_client):
    tenant_client.post("/tenants/self-service",
                       json=_signup_payload("br109-a", "a@br109.example"))
    r = tenant_client.post("/tenants/self-service",
                           json=_signup_payload("br109-b", "b@br109.example"))
    assert r.status_code == 201, r.text


# ------------------------------------------------------------------ promotion
def test_promote_requires_platform_admin(tenant_client, token_for):
    signup = tenant_client.post(
        "/tenants/self-service",
        json=_signup_payload("br109-promo-auth", "promoauth@br109.example")).json()
    r = tenant_client.post(f"/tenants/{signup['id']}/promote",
                           headers=token_for("tenant_admin"))
    assert r.status_code == 403


def test_promote_flips_is_sandbox_and_the_plan(tenant_client, token_for):
    signup = tenant_client.post(
        "/tenants/self-service",
        json=_signup_payload("br109-promo-ok", "promoOk@br109.example")).json()
    r = tenant_client.post(f"/tenants/{signup['id']}/promote",
                           headers=token_for("platform_admin"))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["is_sandbox"] is False
    assert body["plan"] == "standard"


def test_promoting_a_tenant_that_was_never_a_sandbox_is_refused(tenant_client, token_for,
                                                                 tid):
    r = tenant_client.post(f"/tenants/{tid}/promote", headers=token_for("platform_admin"))
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "not_a_sandbox"


def test_promoting_the_same_sandbox_twice_is_refused_the_second_time(tenant_client,
                                                                     token_for):
    signup = tenant_client.post(
        "/tenants/self-service",
        json=_signup_payload("br109-promo-twice", "twice@br109.example")).json()
    first = tenant_client.post(f"/tenants/{signup['id']}/promote",
                               headers=token_for("platform_admin"))
    assert first.status_code == 200
    second = tenant_client.post(f"/tenants/{signup['id']}/promote",
                                headers=token_for("platform_admin"))
    assert second.status_code == 409
    assert second.json()["error"]["code"] == "not_a_sandbox"


# ------------------------------------------------------------------ gateway exposure
def test_only_self_service_is_public_under_the_tenants_segment():
    """Every other /tenants path stays behind the gateway's bearer-token check -
    self-service is a deliberately narrow exception, not a loosened segment."""
    from services.gateway.app.main import _is_public

    assert _is_public("tenants", "tenants/self-service") is True
    assert _is_public("tenants", "tenants") is False
    assert _is_public("tenants", "tenants/abc-123") is False
    assert _is_public("tenants", "tenants/abc-123/promote") is False
    assert _is_public("tenants", "tenants/abc-123/suspend") is False


# ------------------------------------------------------------------ ingestion isolation
def _ingest_token(tenant_id: str) -> dict:
    token = create_access_token(subject="br109-test-cred", role="service",
                                tenant_id=tenant_id, scope="ingest")
    return {"Authorization": f"Bearer {token}"}


def _upi_txn(txn_id: str) -> dict:
    return {"rail": "UPI", "payload": {
        "txn_id": txn_id, "ts": "2026-08-18T09:00:00+05:30", "amount": "500.00",
        "debtor_account": "AC-SANDBOX-DR", "creditor_account": "AC-SANDBOX-CR"}}


def test_a_sandbox_tenant_cannot_submit_batches_labelled_live(tenant_client,
                                                               ingestion_client):
    signup = tenant_client.post(
        "/tenants/self-service",
        json=_signup_payload("br109-iso-live", "isolive@br109.example")).json()
    r = ingestion_client.post(
        f"/ingest/{signup['id']}/transactions",
        headers=_ingest_token(signup["id"]),
        json={"source": "live", "transactions": [_upi_txn("BR109-LIVE-1")]})
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "sandbox_cannot_be_live"


def test_a_sandbox_tenant_can_submit_batches_under_any_other_source_label(
        tenant_client, ingestion_client):
    signup = tenant_client.post(
        "/tenants/self-service",
        json=_signup_payload("br109-iso-ok", "isook@br109.example")).json()
    r = ingestion_client.post(
        f"/ingest/{signup['id']}/transactions",
        headers=_ingest_token(signup["id"]),
        json={"source": "sandbox", "transactions": [_upi_txn("BR109-SANDBOX-1")]})
    assert r.status_code == 202, r.text
    assert r.json()["accepted"] == 1


def test_a_promoted_tenant_may_submit_live_traffic_again(tenant_client, ingestion_client,
                                                          token_for):
    signup = tenant_client.post(
        "/tenants/self-service",
        json=_signup_payload("br109-iso-promoted", "isopromoted@br109.example")).json()
    promoted = tenant_client.post(f"/tenants/{signup['id']}/promote",
                                  headers=token_for("platform_admin"))
    assert promoted.status_code == 200, promoted.text

    from cp_common import tenant_status
    tenant_status.forget(signup["id"])  # this process's own cache, not just tenant-service's

    r = ingestion_client.post(
        f"/ingest/{signup['id']}/transactions",
        headers=_ingest_token(signup["id"]),
        json={"source": "live", "transactions": [_upi_txn("BR109-PROMOTED-LIVE-1")]})
    assert r.status_code == 202, r.text


def test_a_non_sandbox_tenant_is_unaffected(tenant_client, ingestion_client, tid):
    """The seeded shared tenant (not a sandbox) must never trip this check - the
    isolation is specific to is_sandbox, not a blanket restriction on 'live'."""
    r = ingestion_client.post(
        f"/ingest/{tid}/transactions",
        headers=_ingest_token(tid),
        json={"source": "live", "transactions": [_upi_txn("BR109-NONSANDBOX-LIVE-1")]})
    assert r.status_code == 202, r.text
