"""Audit-trail access: the six tenant-scoped roles cp_common.rbac has always granted
the ``audit`` module to (tenant_admin, risk_manager, principal_officer, supervisor,
cro, rbi_inspector) can now actually reach GET /audit for their own tenant - fixing a
pre-existing mismatch where the console showed all six an "Audit" nav item and the API
403'd every one of them. Also covers the CSV export this gap made pointless to build
until now.
"""
import csv
import io
import uuid

import pytest
from sqlalchemy import select

from cp_common.rbac import MOD_AUDIT, MOD_MONITORING, ROLES
from services.tenant_service.app.models import Tenant, TenantUser
from services.tenant_service.app.roles import seed_default_roles

HAS_AUDIT = [name for name, role in ROLES.items()
            if name != "platform_admin" and MOD_AUDIT in role.modules]
LACKS_AUDIT = [name for name, role in ROLES.items()
              if name != "platform_admin" and MOD_AUDIT not in role.modules]


def test_the_fixture_actually_covers_both_kinds_of_role():
    """A guard on the guard: if either list were empty, the tests below would pass
    for the wrong reason."""
    assert HAS_AUDIT and LACKS_AUDIT


@pytest.mark.parametrize("role", HAS_AUDIT)
def test_every_role_the_platform_grants_audit_to_can_now_reach_it(
        tenant_client, token_for, tid, role):
    r = tenant_client.get(f"/audit?tenant_id={tid}", headers=token_for(role))
    assert r.status_code == 200, f"{role} should reach its own tenant's audit: {r.text[:200]}"


@pytest.mark.parametrize("role", LACKS_AUDIT)
def test_a_role_without_the_audit_module_is_still_refused(
        tenant_client, token_for, tid, role):
    r = tenant_client.get(f"/audit?tenant_id={tid}", headers=token_for(role))
    assert r.status_code == 403, f"{role} should not reach audit: {r.status_code}"
    assert r.json()["error"]["code"] == "insufficient_role"


def test_omitting_tenant_id_defaults_to_the_callers_own_tenant(tenant_client, token_for, tid):
    """Previously any tenant_id (or none) 403'd for a tenant-scoped caller outright;
    now omitting it is the normal case, not an error."""
    r = tenant_client.get("/audit", headers=token_for("tenant_admin"))
    assert r.status_code == 200, r.text
    assert all(row["tenant_id"] == tid for row in r.json())


def test_a_tenant_scoped_caller_cannot_see_another_tenants_audit(tenant_client, token_for):
    r = tenant_client.get("/audit?tenant_id=some-other-tenant-id",
                          headers=token_for("tenant_admin"))
    assert r.status_code == 403


def test_platform_admin_still_sees_every_tenant_by_default(tenant_client, token_for):
    r = tenant_client.get("/audit", headers=token_for("platform_admin"))
    assert r.status_code == 200, r.text


def test_action_prefix_filters_to_only_matching_actions(tenant_client, token_for, tid):
    r = tenant_client.get(f"/audit?tenant_id={tid}&action_prefix=role.",
                          headers=token_for("tenant_admin"))
    assert r.status_code == 200, r.text
    assert all(row["action"].startswith("role.") for row in r.json())


def test_unauthenticated_is_refused(tenant_client):
    assert tenant_client.get("/audit").status_code == 401


# ------------------------------------------------------- dynamic (custom) roles
@pytest.fixture()
def audit_tenant(tenant_client, seeded):
    from cp_common import SessionLocal, hash_password
    db = SessionLocal()
    try:
        tenant = Tenant(slug=f"audit-access-{uuid.uuid4().hex[:10]}",
                        legal_name="Audit Access Test Ltd.", display_name="Audit Access",
                        status="active", mfa_policy="optional")
        db.add(tenant)
        db.flush()
        seed_default_roles(db, tenant.id)
        db.commit()
        tid = tenant.id
        slug = tenant.slug
    finally:
        db.close()
    return {"id": tid, "slug": slug}


def _plant_user(tid: str, role: str, tag: str) -> str:
    from cp_common import SessionLocal, hash_password
    db = SessionLocal()
    try:
        email = f"{tag}@{tid}.test.local"
        db.add(TenantUser(tenant_id=tid, email=email, role=role,
                          password_hash=hash_password("AuditAccessPass#2026x")))
        db.commit()
    finally:
        db.close()
    return email


def _login(tenant_client, email: str) -> dict:
    r = tenant_client.post("/auth/login",
                           json={"email": email, "password": "AuditAccessPass#2026x"})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def test_a_custom_role_granted_the_audit_module_can_view_it(tenant_client, audit_tenant):
    from cp_common import SessionLocal
    db = SessionLocal()
    try:
        from services.tenant_service.app import roles as roles_mod
        roles_mod.create_role(
            db, audit_tenant["id"], name="compliance_reviewer",
            label="Compliance Reviewer", modules=[MOD_AUDIT], dashboards=[],
            can_admin_tenant=False, can_reveal_pii=False)
        db.commit()
    finally:
        db.close()
    email = _plant_user(audit_tenant["id"], "compliance_reviewer", "reviewer")
    r = tenant_client.get(f"/audit?tenant_id={audit_tenant['id']}",
                          headers=_login(tenant_client, email))
    assert r.status_code == 200, r.text


def test_a_custom_role_without_the_audit_module_is_refused(tenant_client, audit_tenant):
    from cp_common import SessionLocal
    db = SessionLocal()
    try:
        from services.tenant_service.app import roles as roles_mod
        roles_mod.create_role(
            db, audit_tenant["id"], name="ops_only", label="Ops Only",
            modules=[MOD_MONITORING], dashboards=[], can_admin_tenant=False,
            can_reveal_pii=False)
        db.commit()
    finally:
        db.close()
    email = _plant_user(audit_tenant["id"], "ops_only", "ops")
    r = tenant_client.get(f"/audit?tenant_id={audit_tenant['id']}",
                          headers=_login(tenant_client, email))
    assert r.status_code == 403, r.text
    assert r.json()["error"]["code"] == "insufficient_role"


# ------------------------------------------------------------------ CSV export
def test_csv_export_has_the_expected_header_and_content_type(tenant_client, token_for, tid):
    r = tenant_client.get(f"/audit/export?tenant_id={tid}", headers=token_for("tenant_admin"))
    assert r.status_code == 200, r.text
    assert r.headers["content-type"].startswith("text/csv")
    assert "attachment" in r.headers["content-disposition"]
    reader = csv.reader(io.StringIO(r.text))
    header = next(reader)
    assert header == ["timestamp", "actor", "actor_role", "tenant_id", "action",
                      "target_type", "target_id", "status", "detail"]


def test_csv_export_respects_the_action_prefix_filter(tenant_client, token_for, tid):
    r = tenant_client.get(f"/audit/export?tenant_id={tid}&action_prefix=role.",
                          headers=token_for("tenant_admin"))
    assert r.status_code == 200, r.text
    reader = csv.DictReader(io.StringIO(r.text))
    rows = list(reader)
    assert all(row["action"].startswith("role.") for row in rows)


def test_csv_export_is_scoped_the_same_way_the_json_view_is(tenant_client, token_for):
    r = tenant_client.get("/audit/export?tenant_id=some-other-tenant-id",
                          headers=token_for("tenant_admin"))
    assert r.status_code == 403


def test_a_malicious_action_prefix_cannot_inject_into_the_filename_header(
        tenant_client, token_for, tid):
    """A query param reflected into Content-Disposition is a header-injection surface
    regardless of how implausible the input looks."""
    # Built via params=, not string interpolation: httpx's own URL parser refuses a
    # literal \r\n in the request line, exactly as any real client would - the attack
    # is a percent-encoded value that gets decoded back to \r\n server-side, which
    # params= reproduces faithfully.
    payload = 'role."\r\nX-Injected: yes'
    r = tenant_client.get(
        "/audit/export", params={"tenant_id": tid, "action_prefix": payload},
        headers=token_for("tenant_admin"))
    assert r.status_code == 200, r.text
    assert "\r" not in r.headers["content-disposition"]
    assert "\n" not in r.headers["content-disposition"]
    assert "X-Injected" not in r.headers
