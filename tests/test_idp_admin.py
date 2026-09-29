"""Identity-provider configuration: the admin CRUD behind the console UI (task #21).

Before this, /auth/sso/admin/{tenant_id}/providers could only be read - a bank had no way
to add, change or remove a federation source except a direct database write. These tests
cover the write path: who may use it, what it refuses, and that a client secret behaves
like every other secret in this platform - write-only, never echoed back.
"""
import pytest
from sqlalchemy import select, text

from cp_common.db import SessionLocal
from services.tenant_service.app.idp_models import IdentityProvider
from services.tenant_service.app.models import Tenant

PREFIX = "/auth/sso/admin"


@pytest.fixture
def clean_idps(tid):
    """Providers created by a test are removed afterwards, whether or not it passed."""
    yield
    db = SessionLocal()
    try:
        db.execute(text("DELETE FROM tenant.identity_providers WHERE tenant_id = :t"),
                   {"t": tid})
        db.commit()
    finally:
        db.close()


def _payload(**overrides):
    body = {
        "slug": "corp-entra", "display_name": "Corporate Entra ID",
        "protocol": "oidc", "issuer": "https://login.example/tenant",
        "client_id": "console-app", "client_secret": "s3cr3t",
        "discovery_url": "https://login.example/tenant/.well-known/openid-configuration",
        "role_mapping": {"FRAUD-ANALYSTS": "analyst"}, "default_role": "analyst",
    }
    body.update(overrides)
    return body


# --------------------------------------------------------------------------- creation
def test_a_tenant_admin_can_create_a_provider(tenant_client, token_for, tid, clean_idps):
    r = tenant_client.post(f"{PREFIX}/{tid}/providers", json=_payload(),
                           headers=token_for("tenant_admin"))
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["slug"] == "corp-entra"
    assert body["has_client_secret"] is True
    assert "client_secret" not in body, "the secret must never be echoed back"


def test_an_ordinary_role_may_not_create_a_provider(tenant_client, token_for, tid,
                                                     clean_idps):
    r = tenant_client.post(f"{PREFIX}/{tid}/providers", json=_payload(),
                           headers=token_for("analyst"))
    assert r.status_code == 403


def test_a_reserved_slug_is_refused(tenant_client, token_for, tid, clean_idps):
    r = tenant_client.post(f"{PREFIX}/{tid}/providers", json=_payload(slug="admin"),
                           headers=token_for("tenant_admin"))
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "slug_reserved"


def test_a_duplicate_slug_is_refused(tenant_client, token_for, tid, clean_idps):
    h = token_for("tenant_admin")
    first = tenant_client.post(f"{PREFIX}/{tid}/providers", json=_payload(), headers=h)
    assert first.status_code == 201, first.text
    second = tenant_client.post(f"{PREFIX}/{tid}/providers", json=_payload(), headers=h)
    assert second.status_code == 409
    assert second.json()["error"]["code"] == "slug_conflict"


def test_a_group_cannot_be_mapped_to_an_unassignable_role(tenant_client, token_for, tid,
                                                           clean_idps):
    """A directory group must never become a platform role - see idp_models."""
    r = tenant_client.post(
        f"{PREFIX}/{tid}/providers",
        json=_payload(role_mapping={"IT-ADMINS": "platform_admin"}),
        headers=token_for("tenant_admin"))
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "role_not_assignable"


def test_a_default_role_that_is_not_assignable_is_refused(tenant_client, token_for, tid,
                                                           clean_idps):
    r = tenant_client.post(f"{PREFIX}/{tid}/providers",
                           json=_payload(default_role="platform_admin"),
                           headers=token_for("tenant_admin"))
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "role_not_assignable"


# ---------------------------------------------------------------------------- update
def test_updating_a_field_leaves_the_rest_alone(tenant_client, token_for, tid, clean_idps):
    h = token_for("tenant_admin")
    made = tenant_client.post(f"{PREFIX}/{tid}/providers", json=_payload(),
                              headers=h).json()
    r = tenant_client.patch(f"{PREFIX}/{tid}/providers/{made['id']}",
                            json={"display_name": "Renamed"}, headers=h)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["display_name"] == "Renamed"
    assert body["issuer"] == "https://login.example/tenant", "untouched field changed"


def test_a_blank_secret_on_update_leaves_the_stored_one_alone(tenant_client, token_for,
                                                               tid, clean_idps):
    h = token_for("tenant_admin")
    made = tenant_client.post(f"{PREFIX}/{tid}/providers", json=_payload(),
                              headers=h).json()
    r = tenant_client.patch(f"{PREFIX}/{tid}/providers/{made['id']}",
                            json={"client_secret": ""}, headers=h)
    assert r.status_code == 200, r.text
    assert r.json()["has_client_secret"] is True

    db = SessionLocal()
    try:
        row = db.get(IdentityProvider, made["id"])
        assert row.client_secret == "s3cr3t", "a blank update wiped a working secret"
    finally:
        db.close()


def test_a_non_blank_secret_on_update_replaces_the_stored_one(tenant_client, token_for,
                                                               tid, clean_idps):
    h = token_for("tenant_admin")
    made = tenant_client.post(f"{PREFIX}/{tid}/providers", json=_payload(),
                              headers=h).json()
    tenant_client.patch(f"{PREFIX}/{tid}/providers/{made['id']}",
                        json={"client_secret": "new-secret"}, headers=h)

    db = SessionLocal()
    try:
        row = db.get(IdentityProvider, made["id"])
        assert row.client_secret == "new-secret"
    finally:
        db.close()


def test_updating_to_an_unassignable_role_is_refused(tenant_client, token_for, tid,
                                                      clean_idps):
    h = token_for("tenant_admin")
    made = tenant_client.post(f"{PREFIX}/{tid}/providers", json=_payload(),
                              headers=h).json()
    r = tenant_client.patch(f"{PREFIX}/{tid}/providers/{made['id']}",
                            json={"default_role": "platform_admin"}, headers=h)
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "role_not_assignable"


def test_updating_an_unknown_provider_is_a_404(tenant_client, token_for, tid):
    r = tenant_client.patch(f"{PREFIX}/{tid}/providers/does-not-exist",
                            json={"display_name": "x"}, headers=token_for("tenant_admin"))
    assert r.status_code == 404


# ---------------------------------------------------------------------------- delete
def test_deleting_a_provider_removes_it(tenant_client, token_for, tid, clean_idps):
    h = token_for("tenant_admin")
    made = tenant_client.post(f"{PREFIX}/{tid}/providers", json=_payload(),
                              headers=h).json()
    r = tenant_client.delete(f"{PREFIX}/{tid}/providers/{made['id']}", headers=h)
    assert r.status_code == 204

    listed = tenant_client.get(f"{PREFIX}/{tid}/providers", headers=h).json()
    assert made["id"] not in {p["id"] for p in listed}


def test_an_ordinary_role_may_not_delete_a_provider(tenant_client, token_for, tid,
                                                     clean_idps):
    h = token_for("tenant_admin")
    made = tenant_client.post(f"{PREFIX}/{tid}/providers", json=_payload(),
                              headers=h).json()
    r = tenant_client.delete(f"{PREFIX}/{tid}/providers/{made['id']}",
                             headers=token_for("analyst"))
    assert r.status_code == 403


# ------------------------------------------------------------------- tenant isolation
def test_a_tenant_admin_may_not_configure_another_tenant(tenant_client, token_for, tid,
                                                          clean_idps):
    db = SessionLocal()
    try:
        other = Tenant(slug="other-bank-idp-test", legal_name="Other Bank",
                       display_name="Other Bank", status="active", mfa_policy="optional")
        db.add(other)
        db.commit()
        other_id = other.id
    finally:
        db.close()

    try:
        r = tenant_client.post(f"{PREFIX}/{other_id}/providers", json=_payload(),
                               headers=token_for("tenant_admin"))
        assert r.status_code == 403
    finally:
        db2 = SessionLocal()
        try:
            db2.execute(text("DELETE FROM tenant.tenants WHERE id = :t"), {"t": other_id})
            db2.commit()
        finally:
            db2.close()
