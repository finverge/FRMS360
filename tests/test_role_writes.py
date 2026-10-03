"""BR-113 phase 2b: a tenant defines and edits its own roles.

Mirrors test_policy_attestation.py's weighting - mostly refusals, because the whole
failure mode here is a role quietly ending up with more reach than it should, or a
platform-only surface leaking into a tenant's own catalogue.
"""
import uuid

import pytest
from sqlalchemy import select

from cp_common.rbac import ASSIGNABLE_TENANT_ROLES, MOD_ADMIN, MOD_MONITORING
from services.tenant_service.app.models import Tenant, TenantRole, TenantUser
from services.tenant_service.app.roles import seed_default_roles


@pytest.fixture()
def role_tenant(tenant_client, seeded):
    """A tenant with its own materialised role catalogue (BR-113 phase 2a), separate
    from the shared `tid` fixture so write-path tests never mutate the catalogue every
    other test file's `tid` assertions depend on."""
    from cp_common import SessionLocal, hash_password

    db = SessionLocal()
    try:
        tenant = Tenant(slug=f"role-writes-{uuid.uuid4().hex[:10]}",
                        legal_name="Role Writes Test Ltd.", display_name="Role Writes",
                        status="active", mfa_policy="optional")
        db.add(tenant)
        db.flush()
        admin = TenantUser(tenant_id=tenant.id, email=f"admin@{tenant.slug}.test.local",
                           role="tenant_admin",
                           password_hash=hash_password("RoleWritesPass#2026x"))
        second = TenantUser(tenant_id=tenant.id, email=f"second@{tenant.slug}.test.local",
                            role="tenant_admin",
                            password_hash=hash_password("RoleWritesPass#2026x"))
        db.add_all([admin, second])
        seed_default_roles(db, tenant.id)
        db.commit()
        tid = tenant.id
    finally:
        db.close()
    yield tid


def _login(tenant_client, email: str) -> dict:
    r = tenant_client.post("/auth/login",
                           json={"email": email, "password": "RoleWritesPass#2026x"})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def _admin_headers(tenant_client, tid: str) -> dict:
    return _login(tenant_client, f"admin@{_slug(tid)}.test.local")


def _second_headers(tenant_client, tid: str) -> dict:
    return _login(tenant_client, f"second@{_slug(tid)}.test.local")


def _slug(tid: str) -> str:
    from cp_common import SessionLocal
    db = SessionLocal()
    try:
        return db.get(Tenant, tid).slug
    finally:
        db.close()


GOOD = {"name": "regional_fraud_lead", "label": "Regional Fraud Lead",
        "modules": [MOD_MONITORING], "dashboards": ["analyst", "ews"],
        "can_admin_tenant": False, "can_reveal_pii": False}


# --------------------------------------------------------------------- create
def test_a_tenant_admin_can_create_a_custom_role(tenant_client, role_tenant):
    h = _admin_headers(tenant_client, role_tenant)
    r = tenant_client.post(f"/tenants/{role_tenant}/roles", headers=h, json=GOOD)
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["name"] == "regional_fraud_lead"
    assert body["source"] == "custom"
    assert body["member_count"] == 0


def test_the_created_role_appears_in_the_catalogue(tenant_client, role_tenant):
    h = _admin_headers(tenant_client, role_tenant)
    tenant_client.post(f"/tenants/{role_tenant}/roles", headers=h, json=GOOD)
    r = tenant_client.get(f"/tenants/{role_tenant}/roles", headers=h)
    names = {row["name"] for row in r.json()}
    assert "regional_fraud_lead" in names
    assert names == set(ASSIGNABLE_TENANT_ROLES) | {"regional_fraud_lead"}


def test_the_platform_operators_name_is_reserved(tenant_client, role_tenant):
    h = _admin_headers(tenant_client, role_tenant)
    r = tenant_client.post(f"/tenants/{role_tenant}/roles", headers=h,
                           json={**GOOD, "name": "platform_admin"})
    assert r.status_code == 422, r.text
    assert r.json()["error"]["code"] == "reserved_name"


@pytest.mark.parametrize("existing", ["tenant_admin", "analyst"])
def test_a_starter_name_is_an_ordinary_name_taken_while_its_row_exists(
        tenant_client, role_tenant, existing):
    """The ten starter names are not special any more; they are simply in use."""
    h = _admin_headers(tenant_client, role_tenant)
    r = tenant_client.post(f"/tenants/{role_tenant}/roles", headers=h,
                           json={**GOOD, "name": existing})
    assert r.status_code == 422, r.text
    assert r.json()["error"]["code"] == "name_exists"


def test_a_duplicate_custom_name_is_refused(tenant_client, role_tenant):
    h = _admin_headers(tenant_client, role_tenant)
    tenant_client.post(f"/tenants/{role_tenant}/roles", headers=h, json=GOOD)
    r = tenant_client.post(f"/tenants/{role_tenant}/roles", headers=h, json=GOOD)
    assert r.status_code == 422, r.text
    assert r.json()["error"]["code"] == "name_exists"


def test_module_admin_can_never_be_granted_to_a_tenant_role(tenant_client, role_tenant):
    h = _admin_headers(tenant_client, role_tenant)
    r = tenant_client.post(f"/tenants/{role_tenant}/roles", headers=h,
                           json={**GOOD, "modules": [MOD_ADMIN]})
    assert r.status_code == 422, r.text
    assert r.json()["error"]["code"] == "role_guardrail"


def test_the_tenant_health_dashboard_can_never_be_granted(tenant_client, role_tenant):
    h = _admin_headers(tenant_client, role_tenant)
    r = tenant_client.post(f"/tenants/{role_tenant}/roles", headers=h,
                           json={**GOOD, "dashboards": ["tenant_health"]})
    assert r.status_code == 422, r.text
    assert r.json()["error"]["code"] == "role_guardrail"


def test_an_unknown_module_or_dashboard_is_refused(tenant_client, role_tenant):
    h = _admin_headers(tenant_client, role_tenant)
    r = tenant_client.post(f"/tenants/{role_tenant}/roles", headers=h,
                           json={**GOOD, "dashboards": ["not_a_real_dashboard"]})
    assert r.status_code == 422, r.text


def test_creating_a_role_is_audited(tenant_client, role_tenant):
    from cp_common import SessionLocal
    from cp_common.audit import AuditLog
    h = _admin_headers(tenant_client, role_tenant)
    tenant_client.post(f"/tenants/{role_tenant}/roles", headers=h, json=GOOD)
    db = SessionLocal()
    try:
        row = db.scalar(select(AuditLog).where(
            AuditLog.tenant_id == role_tenant, AuditLog.action == "role.create",
            AuditLog.target_id == "regional_fraud_lead"))
        assert row is not None
        assert row.detail["name"] == "regional_fraud_lead"
    finally:
        db.close()


def test_a_non_admin_role_cannot_create_a_role(tenant_client, role_tenant):
    from cp_common import SessionLocal, hash_password
    db = SessionLocal()
    try:
        db.add(TenantUser(tenant_id=role_tenant, email=f"an@{_slug(role_tenant)}.test.local",
                          role="analyst", password_hash=hash_password("RoleWritesPass#2026x")))
        db.commit()
    finally:
        db.close()
    h = _login(tenant_client, f"an@{_slug(role_tenant)}.test.local")
    r = tenant_client.post(f"/tenants/{role_tenant}/roles", headers=h, json=GOOD)
    assert r.status_code == 403, r.text
    assert r.json()["error"]["code"] == "insufficient_role"


# --------------------------------------------------------------------- assignment
def test_a_custom_role_can_then_be_assigned_to_an_invited_user(
        tenant_client, token_for, role_tenant, monkeypatch):
    import services.tenant_service.app.services as svc_module

    class _Fake:
        status_code = 200
        def raise_for_status(self): pass
        def json(self): return {}

    monkeypatch.setattr(svc_module.httpx, "put", lambda *a, **k: _Fake())
    monkeypatch.setattr(svc_module.httpx, "post", lambda *a, **k: _Fake())

    # POST /{tenant_id}/users is platform_admin-only today - a pre-existing,
    # documented gap (FSD's "Inviting a tenant's own users is not yet a Tenant
    # Administrator action") unrelated to BR-113 and deliberately not touched here.
    # tenant_admin creates the role; platform_admin does the inviting, exactly as the
    # rest of the platform already has to.
    h = _admin_headers(tenant_client, role_tenant)
    tenant_client.post(f"/tenants/{role_tenant}/roles", headers=h, json=GOOD)
    r = tenant_client.post(f"/tenants/{role_tenant}/users", headers=token_for("platform_admin"),
                           json={"email": f"lead@{_slug(role_tenant)}.test.local",
                                 "role": "regional_fraud_lead"})
    assert r.status_code == 201, r.text
    assert r.json()["role"] == "regional_fraud_lead"


def test_a_role_that_does_not_exist_for_this_tenant_cannot_be_assigned(
        tenant_client, token_for, role_tenant):
    r = tenant_client.post(f"/tenants/{role_tenant}/users", headers=token_for("platform_admin"),
                           json={"email": f"ghost@{_slug(role_tenant)}.test.local",
                                 "role": "does_not_exist"})
    assert r.status_code == 422, r.text
    assert r.json()["error"]["code"] == "unknown_role"


def test_platform_admin_is_never_an_assignable_role(tenant_client, token_for, role_tenant):
    r = tenant_client.post(f"/tenants/{role_tenant}/users", headers=token_for("platform_admin"),
                           json={"email": f"pa@{_slug(role_tenant)}.test.local",
                                 "role": "platform_admin"})
    assert r.status_code == 422, r.text


# --------------------------------------------------------------------- update
def test_a_non_elevating_edit_applies_immediately(tenant_client, role_tenant):
    h = _admin_headers(tenant_client, role_tenant)
    tenant_client.post(f"/tenants/{role_tenant}/roles", headers=h, json=GOOD)
    r = tenant_client.put(f"/tenants/{role_tenant}/roles/regional_fraud_lead",
                          headers=h, json={"label": "Regional Lead, Fraud Ops"})
    assert r.status_code == 200, r.text
    assert r.json()["label"] == "Regional Lead, Fraud Ops"
    assert r.json()["elevation_pending"] is False


def test_editing_a_seeded_role_marks_it_custom(tenant_client, role_tenant):
    h = _admin_headers(tenant_client, role_tenant)
    r = tenant_client.put(f"/tenants/{role_tenant}/roles/analyst", headers=h,
                          json={"label": "Tier-1 Fraud Reviewer"})
    assert r.status_code == 200, r.text
    assert r.json()["source"] == "custom"


def test_lowering_a_privilege_applies_immediately(tenant_client, role_tenant):
    h = _admin_headers(tenant_client, role_tenant)
    tenant_client.post(f"/tenants/{role_tenant}/roles", headers=h,
                       json={**GOOD, "can_reveal_pii": True})
    r = tenant_client.put(f"/tenants/{role_tenant}/roles/regional_fraud_lead",
                          headers=h, json={"can_reveal_pii": False})
    assert r.status_code == 200, r.text
    assert r.json()["can_reveal_pii"] is False
    assert r.json()["elevation_pending"] is False


def test_raising_a_privilege_is_staged_not_applied(tenant_client, role_tenant):
    h = _admin_headers(tenant_client, role_tenant)
    tenant_client.post(f"/tenants/{role_tenant}/roles", headers=h, json=GOOD)
    r = tenant_client.put(f"/tenants/{role_tenant}/roles/regional_fraud_lead",
                          headers=h, json={"can_reveal_pii": True})
    assert r.status_code == 200, r.text
    assert r.json()["can_reveal_pii"] is False  # not yet applied
    assert r.json()["elevation_pending"] is True


def test_an_update_still_enforces_the_module_guardrail(tenant_client, role_tenant):
    h = _admin_headers(tenant_client, role_tenant)
    tenant_client.post(f"/tenants/{role_tenant}/roles", headers=h, json=GOOD)
    r = tenant_client.put(f"/tenants/{role_tenant}/roles/regional_fraud_lead",
                          headers=h, json={"modules": [MOD_ADMIN]})
    assert r.status_code == 422, r.text
    assert r.json()["error"]["code"] == "role_guardrail"


def test_raising_can_activate_config_is_staged_like_the_other_two_flags(
        tenant_client, role_tenant):
    """The risk_manager-equivalent grant follows the same maker-checker asymmetry as
    can_admin_tenant/can_reveal_pii - it does not get to skip elevation just because
    it is narrower than full admin."""
    h = _admin_headers(tenant_client, role_tenant)
    tenant_client.post(f"/tenants/{role_tenant}/roles", headers=h, json=GOOD)
    r = tenant_client.put(f"/tenants/{role_tenant}/roles/regional_fraud_lead",
                          headers=h, json={"can_activate_config": True})
    assert r.status_code == 200, r.text
    assert r.json()["can_activate_config"] is False  # not yet applied
    assert r.json()["elevation_pending"] is True


def test_a_different_admin_confirming_applies_can_activate_config_too(
        tenant_client, role_tenant):
    h1 = _admin_headers(tenant_client, role_tenant)
    h2 = _second_headers(tenant_client, role_tenant)
    tenant_client.post(f"/tenants/{role_tenant}/roles", headers=h1, json=GOOD)
    tenant_client.put(f"/tenants/{role_tenant}/roles/regional_fraud_lead", headers=h1,
                      json={"can_activate_config": True})
    r = tenant_client.post(
        f"/tenants/{role_tenant}/roles/regional_fraud_lead/confirm", headers=h2)
    assert r.status_code == 200, r.text
    assert r.json()["can_activate_config"] is True
    assert r.json()["elevation_pending"] is False


def test_lowering_can_activate_config_applies_immediately(tenant_client, role_tenant):
    h = _admin_headers(tenant_client, role_tenant)
    tenant_client.post(f"/tenants/{role_tenant}/roles", headers=h,
                       json={**GOOD, "can_activate_config": True})
    r = tenant_client.put(f"/tenants/{role_tenant}/roles/regional_fraud_lead",
                          headers=h, json={"can_activate_config": False})
    assert r.status_code == 200, r.text
    assert r.json()["can_activate_config"] is False
    assert r.json()["elevation_pending"] is False


def test_editing_records_a_full_before_after_snapshot(tenant_client, role_tenant):
    from cp_common import SessionLocal
    from cp_common.audit import AuditLog
    h = _admin_headers(tenant_client, role_tenant)
    tenant_client.post(f"/tenants/{role_tenant}/roles", headers=h, json=GOOD)
    tenant_client.put(f"/tenants/{role_tenant}/roles/regional_fraud_lead", headers=h,
                      json={"label": "Renamed"})
    db = SessionLocal()
    try:
        row = db.scalar(select(AuditLog).where(
            AuditLog.tenant_id == role_tenant, AuditLog.action == "role.update",
            AuditLog.target_id == "regional_fraud_lead"))
        assert row.detail["before"]["label"] == "Regional Fraud Lead"
        assert row.detail["after"]["label"] == "Renamed"
    finally:
        db.close()


def test_updating_an_unknown_role_is_404(tenant_client, role_tenant):
    h = _admin_headers(tenant_client, role_tenant)
    r = tenant_client.put(f"/tenants/{role_tenant}/roles/does_not_exist", headers=h,
                          json={"label": "x"})
    assert r.status_code == 404


# --------------------------------------------------------------------- confirm
def test_a_different_admin_can_confirm_an_elevation(tenant_client, role_tenant):
    h1 = _admin_headers(tenant_client, role_tenant)
    h2 = _second_headers(tenant_client, role_tenant)
    tenant_client.post(f"/tenants/{role_tenant}/roles", headers=h1, json=GOOD)
    tenant_client.put(f"/tenants/{role_tenant}/roles/regional_fraud_lead", headers=h1,
                      json={"can_reveal_pii": True})
    r = tenant_client.post(
        f"/tenants/{role_tenant}/roles/regional_fraud_lead/confirm", headers=h2)
    assert r.status_code == 200, r.text
    assert r.json()["can_reveal_pii"] is True
    assert r.json()["elevation_pending"] is False


def test_the_proposer_cannot_confirm_their_own_elevation(tenant_client, role_tenant):
    h1 = _admin_headers(tenant_client, role_tenant)
    tenant_client.post(f"/tenants/{role_tenant}/roles", headers=h1, json=GOOD)
    tenant_client.put(f"/tenants/{role_tenant}/roles/regional_fraud_lead", headers=h1,
                      json={"can_reveal_pii": True})
    r = tenant_client.post(
        f"/tenants/{role_tenant}/roles/regional_fraud_lead/confirm", headers=h1)
    assert r.status_code == 403, r.text
    assert r.json()["error"]["code"] == "self_approval"


def test_confirming_with_nothing_pending_is_refused(tenant_client, role_tenant):
    h = _admin_headers(tenant_client, role_tenant)
    tenant_client.post(f"/tenants/{role_tenant}/roles", headers=h, json=GOOD)
    r = tenant_client.post(
        f"/tenants/{role_tenant}/roles/regional_fraud_lead/confirm", headers=h)
    assert r.status_code == 422, r.text
    assert r.json()["error"]["code"] == "no_pending_change"


# --------------------------------------------------------------------- delete
def test_a_custom_role_with_no_members_can_be_deleted(tenant_client, role_tenant):
    h = _admin_headers(tenant_client, role_tenant)
    tenant_client.post(f"/tenants/{role_tenant}/roles", headers=h, json=GOOD)
    r = tenant_client.delete(
        f"/tenants/{role_tenant}/roles/regional_fraud_lead", headers=h)
    assert r.status_code == 204, r.text
    names = {row["name"] for row in
             tenant_client.get(f"/tenants/{role_tenant}/roles", headers=h).json()}
    assert "regional_fraud_lead" not in names


def test_a_starter_role_nobody_holds_can_be_deleted_like_any_other(tenant_client, role_tenant):
    h = _admin_headers(tenant_client, role_tenant)
    r = tenant_client.delete(f"/tenants/{role_tenant}/roles/analyst", headers=h)
    assert r.status_code == 204, r.text
    names = {row["name"] for row in
             tenant_client.get(f"/tenants/{role_tenant}/roles", headers=h).json()}
    assert "analyst" not in names


def test_a_refused_delete_is_audited_too_not_only_a_successful_one(
        tenant_client, role_tenant):
    """A refusal is itself a security-relevant event and must leave the same trail a
    successful change does. Here: the only administrator role, still held by two people."""
    from cp_common import SessionLocal
    from cp_common.audit import AuditLog
    h = _admin_headers(tenant_client, role_tenant)
    r = tenant_client.delete(f"/tenants/{role_tenant}/roles/tenant_admin", headers=h)
    assert r.status_code == 409, r.text
    db = SessionLocal()
    try:
        row = db.scalar(select(AuditLog).where(
            AuditLog.tenant_id == role_tenant, AuditLog.action == "role.delete",
            AuditLog.target_id == "tenant_admin", AuditLog.status == "failure"))
        assert row is not None
        assert row.detail["error"] == "role_in_use"
    finally:
        db.close()


def test_a_role_still_held_by_a_user_cannot_be_deleted(
        tenant_client, token_for, role_tenant, monkeypatch):
    import services.tenant_service.app.services as svc_module

    class _Fake:
        status_code = 200
        def raise_for_status(self): pass
        def json(self): return {}

    monkeypatch.setattr(svc_module.httpx, "put", lambda *a, **k: _Fake())
    monkeypatch.setattr(svc_module.httpx, "post", lambda *a, **k: _Fake())

    h = _admin_headers(tenant_client, role_tenant)
    tenant_client.post(f"/tenants/{role_tenant}/roles", headers=h, json=GOOD)
    invited = tenant_client.post(
        f"/tenants/{role_tenant}/users", headers=token_for("platform_admin"),
        json={"email": f"held@{_slug(role_tenant)}.test.local",
              "role": "regional_fraud_lead"})
    assert invited.status_code == 201, invited.text
    r = tenant_client.delete(
        f"/tenants/{role_tenant}/roles/regional_fraud_lead", headers=h)
    assert r.status_code == 409, r.text
    assert r.json()["error"]["code"] == "role_in_use"


# ------------------------------------------------------- capability dependency
def test_a_custom_role_granted_can_admin_tenant_can_itself_manage_roles(
        tenant_client, role_tenant):
    """The capability check is dynamic, not a hardcoded name list - a custom role
    that grants can_admin_tenant can do everything tenant_admin can here.

    The user is planted directly (not via POST .../users) so this test exercises only
    the thing it is named for - the capability dependency - independent of the
    separate invite-flow temp-password dance already covered above."""
    from cp_common import SessionLocal, hash_password
    h = _admin_headers(tenant_client, role_tenant)
    tenant_client.post(f"/tenants/{role_tenant}/roles", headers=h,
                       json={**GOOD, "name": "sub_admin", "can_admin_tenant": True})
    db = SessionLocal()
    try:
        db.add(TenantUser(tenant_id=role_tenant, email=f"sub@{_slug(role_tenant)}.test.local",
                          role="sub_admin",
                          password_hash=hash_password("RoleWritesPass#2026x")))
        db.commit()
    finally:
        db.close()
    h_sub = _login(tenant_client, f"sub@{_slug(role_tenant)}.test.local")
    r = tenant_client.post(f"/tenants/{role_tenant}/roles", headers=h_sub,
                           json={**GOOD, "name": "another_role"})
    assert r.status_code == 201, r.text


def test_cross_tenant_role_management_is_still_refused(tenant_client, role_tenant, tid):
    """require_capability keeps resolve_tenant_scope's existing cross-tenant check -
    a tenant_admin of one tenant cannot manage another tenant's roles."""
    h = _admin_headers(tenant_client, role_tenant)
    r = tenant_client.post(f"/tenants/{tid}/roles", headers=h, json=GOOD)
    assert r.status_code == 403, r.text
