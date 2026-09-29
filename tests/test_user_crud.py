"""tenant_admin gains create/update/delete on its own users - previously all three
were platform_admin-only (create/delete) or did not exist at all (update).

Mirrors test_role_writes.py's weighting: mostly refusals, because the failure mode
that matters is a tenant accidentally stranding itself with nobody able to administer
it, not a tenant being blocked from a legitimate change.
"""
import uuid

import pytest
from sqlalchemy import select

from services.tenant_service.app.models import Tenant, TenantUser
from services.tenant_service.app.roles import seed_default_roles

PASSWORD = "UserCrudTest#2026x"


@pytest.fixture()
def crud_tenant(tenant_client, seeded):
    """Two tenant_admin-role users (so admin-capable-removal tests have a legitimate
    successor) plus one analyst, in a tenant separate from the shared `tid` fixture -
    this file mutates users, which the shared fixture's other consumers cannot afford."""
    from cp_common import SessionLocal, hash_password
    db = SessionLocal()
    try:
        tenant = Tenant(slug=f"user-crud-{uuid.uuid4().hex[:10]}",
                        legal_name="User CRUD Test Ltd.", display_name="User CRUD",
                        status="active", mfa_policy="optional")
        db.add(tenant)
        db.flush()
        admin_a = TenantUser(tenant_id=tenant.id, email=f"admin-a@{tenant.slug}.test.local",
                             role="tenant_admin", password_hash=hash_password(PASSWORD))
        admin_b = TenantUser(tenant_id=tenant.id, email=f"admin-b@{tenant.slug}.test.local",
                             role="tenant_admin", password_hash=hash_password(PASSWORD))
        an = TenantUser(tenant_id=tenant.id, email=f"an@{tenant.slug}.test.local",
                        role="analyst", password_hash=hash_password(PASSWORD))
        db.add_all([admin_a, admin_b, an])
        seed_default_roles(db, tenant.id)
        db.commit()
        ids = {"tenant": tenant.id, "slug": tenant.slug, "admin_a": admin_a.id,
               "admin_b": admin_b.id, "analyst": an.id}
    finally:
        db.close()
    return ids


def _login(tenant_client, email: str) -> dict:
    r = tenant_client.post("/auth/login", json={"email": email, "password": PASSWORD})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def _admin_a(tenant_client, ids):
    return _login(tenant_client, f"admin-a@{ids['slug']}.test.local")


def _analyst(tenant_client, ids):
    return _login(tenant_client, f"an@{ids['slug']}.test.local")


# --------------------------------------------------------------------- create
def test_tenant_admin_can_now_invite_a_user(tenant_client, crud_tenant):
    h = _admin_a(tenant_client, crud_tenant)
    r = tenant_client.post(f"/tenants/{crud_tenant['tenant']}/users", headers=h,
                           json={"email": f"new@{crud_tenant['slug']}.test.local",
                                 "role": "investigator"})
    assert r.status_code == 201, r.text


def test_a_non_admin_capable_role_still_cannot_invite(tenant_client, crud_tenant):
    h = _analyst(tenant_client, crud_tenant)
    r = tenant_client.post(f"/tenants/{crud_tenant['tenant']}/users", headers=h,
                           json={"email": f"x@{crud_tenant['slug']}.test.local",
                                 "role": "analyst"})
    assert r.status_code == 403, r.text
    assert r.json()["error"]["code"] == "insufficient_role"


def test_platform_admin_can_still_invite(tenant_client, token_for, crud_tenant):
    r = tenant_client.post(f"/tenants/{crud_tenant['tenant']}/users",
                           headers=token_for("platform_admin"),
                           json={"email": f"pa-invited@{crud_tenant['slug']}.test.local",
                                 "role": "analyst"})
    assert r.status_code == 201, r.text


# --------------------------------------------------------------------- update
def test_tenant_admin_can_update_a_users_role(tenant_client, crud_tenant):
    h = _admin_a(tenant_client, crud_tenant)
    r = tenant_client.put(f"/tenants/{crud_tenant['tenant']}/users/{crud_tenant['analyst']}",
                          headers=h, json={"role": "risk_manager"})
    assert r.status_code == 200, r.text
    assert r.json()["role"] == "risk_manager"


def test_update_rejects_a_role_the_tenant_does_not_have(tenant_client, crud_tenant):
    h = _admin_a(tenant_client, crud_tenant)
    r = tenant_client.put(f"/tenants/{crud_tenant['tenant']}/users/{crud_tenant['analyst']}",
                          headers=h, json={"role": "does_not_exist"})
    assert r.status_code == 422, r.text
    assert r.json()["error"]["code"] == "unknown_role"


def test_a_non_admin_capable_role_cannot_update_anyone(tenant_client, crud_tenant):
    h = _analyst(tenant_client, crud_tenant)
    r = tenant_client.put(f"/tenants/{crud_tenant['tenant']}/users/{crud_tenant['admin_a']}",
                          headers=h, json={"role": "board"})
    assert r.status_code == 403, r.text


def test_demoting_the_only_admin_capable_user_is_refused(tenant_client, crud_tenant):
    """admin_b and the analyst exist too, but admin_b is a *different* user - demoting
    admin_a alone must still be checked against everyone else's actual capability,
    which here is fine (admin_b remains). This test demotes admin_b first so admin_a
    becomes the sole admin-capable user, then checks demoting admin_a is refused."""
    h = _admin_a(tenant_client, crud_tenant)
    first = tenant_client.put(
        f"/tenants/{crud_tenant['tenant']}/users/{crud_tenant['admin_b']}",
        headers=h, json={"role": "analyst"})
    assert first.status_code == 200, first.text

    r = tenant_client.put(f"/tenants/{crud_tenant['tenant']}/users/{crud_tenant['admin_a']}",
                          headers=h, json={"role": "analyst"})
    assert r.status_code == 409, r.text
    assert r.json()["error"]["code"] == "last_admin"


def test_demoting_one_of_two_admin_capable_users_is_fine(tenant_client, crud_tenant):
    h = _admin_a(tenant_client, crud_tenant)
    r = tenant_client.put(f"/tenants/{crud_tenant['tenant']}/users/{crud_tenant['admin_b']}",
                          headers=h, json={"role": "analyst"})
    assert r.status_code == 200, r.text


def test_updating_an_unknown_user_is_404(tenant_client, crud_tenant):
    h = _admin_a(tenant_client, crud_tenant)
    r = tenant_client.put(f"/tenants/{crud_tenant['tenant']}/users/does-not-exist",
                          headers=h, json={"role": "analyst"})
    assert r.status_code == 404


def test_update_is_audited_with_before_and_after(tenant_client, crud_tenant):
    from cp_common import SessionLocal
    from cp_common.audit import AuditLog
    h = _admin_a(tenant_client, crud_tenant)
    tenant_client.put(f"/tenants/{crud_tenant['tenant']}/users/{crud_tenant['analyst']}",
                      headers=h, json={"role": "supervisor"})
    db = SessionLocal()
    try:
        row = db.scalar(select(AuditLog).where(
            AuditLog.tenant_id == crud_tenant["tenant"], AuditLog.action == "user.update",
            AuditLog.target_id == crud_tenant["analyst"]))
        assert row is not None
        assert row.detail["role_from"] == "analyst"
        assert row.detail["role_to"] == "supervisor"
    finally:
        db.close()


# --------------------------------------------------------------------- delete
def test_tenant_admin_can_now_remove_a_user(tenant_client, crud_tenant):
    h = _admin_a(tenant_client, crud_tenant)
    r = tenant_client.delete(f"/tenants/{crud_tenant['tenant']}/users/{crud_tenant['analyst']}",
                             headers=h)
    assert r.status_code == 204, r.text


def test_a_non_admin_capable_role_still_cannot_remove_anyone(tenant_client, crud_tenant):
    h = _analyst(tenant_client, crud_tenant)
    r = tenant_client.delete(f"/tenants/{crud_tenant['tenant']}/users/{crud_tenant['admin_b']}",
                             headers=h)
    assert r.status_code == 403, r.text


def test_removing_the_only_admin_capable_user_is_refused_even_with_others_present(
        tenant_client, crud_tenant):
    """The tenant has 3 users; removing admin_a would leave 2 (admin_b, analyst) -
    the old `len(users) <= 1` check would have allowed this. It must not."""
    h = _admin_a(tenant_client, crud_tenant)
    demote = tenant_client.put(
        f"/tenants/{crud_tenant['tenant']}/users/{crud_tenant['admin_b']}",
        headers=h, json={"role": "analyst"})
    assert demote.status_code == 200, demote.text

    r = tenant_client.delete(f"/tenants/{crud_tenant['tenant']}/users/{crud_tenant['admin_a']}",
                             headers=h)
    assert r.status_code == 409, r.text
    assert r.json()["error"]["code"] == "last_admin"


def test_removing_the_tenants_only_remaining_user_is_still_refused(tenant_client, crud_tenant):
    h = _admin_a(tenant_client, crud_tenant)
    tenant_client.delete(f"/tenants/{crud_tenant['tenant']}/users/{crud_tenant['admin_b']}",
                         headers=h)
    tenant_client.delete(f"/tenants/{crud_tenant['tenant']}/users/{crud_tenant['analyst']}",
                         headers=h)
    r = tenant_client.delete(f"/tenants/{crud_tenant['tenant']}/users/{crud_tenant['admin_a']}",
                             headers=h)
    assert r.status_code == 409, r.text
    assert r.json()["error"]["code"] == "last_user"


def test_platform_admin_can_still_remove(tenant_client, token_for, crud_tenant):
    r = tenant_client.delete(f"/tenants/{crud_tenant['tenant']}/users/{crud_tenant['analyst']}",
                             headers=token_for("platform_admin"))
    assert r.status_code == 204, r.text


def test_cross_tenant_user_management_is_still_refused(tenant_client, crud_tenant, tid):
    h = _admin_a(tenant_client, crud_tenant)
    r = tenant_client.delete(f"/tenants/{tid}/users/{crud_tenant['analyst']}", headers=h)
    assert r.status_code == 403
