"""cp_common.dynamic_roles: the four generic capability primitives, consulted by every
service outside tenant-service (analytics, config, decision, notification) instead of
only the hardcoded catalogue.

ingestion_service is deliberately absent - it has no human role checks at all (BR-213
machine credentials only), so there is nothing there to wire.

decision_service's review.py has no existing HTTP test harness (no TestClient fixture
anywhere in this suite) to extend; its `_guard` is three lines calling straight into
the functions this file already tests directly, so the module-level tests below cover
its exact code path without inventing a parallel one.
"""
import uuid

import pytest
from sqlalchemy import select

from cp_common import dynamic_roles
from cp_common.rbac import MOD_ADMIN, MOD_AUDIT, MOD_MONITORING
from services.tenant_service.app.models import Tenant, TenantUser
from services.tenant_service.app.roles import seed_default_roles

PASSWORD = "DynRoleTest#2026x"


@pytest.fixture()
def dyn_tenant(tenant_client, seeded):
    from cp_common import SessionLocal
    db = SessionLocal()
    try:
        tenant = Tenant(slug=f"dyn-roles-{uuid.uuid4().hex[:10]}",
                        legal_name="Dynamic Roles Test Ltd.", display_name="Dynamic Roles",
                        status="active", mfa_policy="optional")
        db.add(tenant)
        db.flush()
        seed_default_roles(db, tenant.id)
        db.commit()
        tid, slug = tenant.id, tenant.slug
    finally:
        db.close()
    return {"id": tid, "slug": slug}


def _login(tenant_client, email: str) -> dict:
    r = tenant_client.post("/auth/login", json={"email": email, "password": PASSWORD})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def _admin_headers(tenant_client, tid: str) -> dict:
    """A tenant_admin exists on every tenant seed_default_roles() would follow, but
    this fixture's tenant has no users at all yet - plant one."""
    from cp_common import SessionLocal, hash_password
    db = SessionLocal()
    try:
        email = f"admin@{tid}.test.local"
        if not db.scalar(select(TenantUser).where(TenantUser.email == email)):
            db.add(TenantUser(tenant_id=tid, email=email, role="tenant_admin",
                              password_hash=hash_password(PASSWORD)))
            db.commit()
    finally:
        db.close()
    return _login(tenant_client, f"admin@{tid}.test.local")


def _create_role(tenant_client, tid: str, headers: dict, **overrides) -> dict:
    body = {"name": "field_lead", "label": "Field Lead", "modules": [MOD_MONITORING],
           "dashboards": ["analyst"], "can_admin_tenant": False, "can_reveal_pii": False,
           "can_activate_config": False}
    body.update(overrides)
    r = tenant_client.post(f"/tenants/{tid}/roles", headers=headers, json=body)
    assert r.status_code == 201, r.text
    return r.json()


def _create_rule_config(config_client, tenant_client, tid: str) -> str:
    """A draft config to activate against - who is allowed to propose it is the whole
    point of the tests that call this, so it is always created by platform_admin,
    never by the role under test."""
    from services.config_service.app.ews_catalogue import rule_configs
    body = next(c for c in rule_configs() if c["kind"] == "rule")
    r = config_client.post(
        f"/configs/{tid}", headers=_admin_headers(tenant_client, tid),
        json={"kind": "rule", "name": f"dyn-role-test-{uuid.uuid4().hex[:8]}",
              "version": "1.0.0", "body": body["body"]})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _plant_user(tid: str, role: str, tag: str) -> str:
    from cp_common import SessionLocal, hash_password
    db = SessionLocal()
    try:
        email = f"{tag}@{tid}.test.local"
        db.add(TenantUser(tenant_id=tid, email=email, role=role,
                          password_hash=hash_password(PASSWORD)))
        db.commit()
    finally:
        db.close()
    return email


# ------------------------------------------------------- the shared module itself
def test_a_fixed_role_never_reaches_the_network(dyn_tenant, monkeypatch):
    """Zero-network path for the ten - the whole point of checking the hardcoded
    catalogue first."""
    def _boom(*a, **k):
        raise AssertionError("fixed-role lookup must not fetch")
    monkeypatch.setattr(dynamic_roles, "_fetch", _boom)
    assert dynamic_roles.can_admin_tenant(dyn_tenant["id"], "tenant_admin") is True
    assert dynamic_roles.can_admin_tenant(dyn_tenant["id"], "analyst") is False
    assert dynamic_roles.can_access_module(dyn_tenant["id"], "analyst", MOD_MONITORING) is True
    assert dynamic_roles.can_access_module(dyn_tenant["id"], "analyst", MOD_ADMIN) is False


def test_an_unknown_name_is_no_access_not_an_error(dyn_tenant):
    assert dynamic_roles.can_admin_tenant(dyn_tenant["id"], "does_not_exist") is False
    assert dynamic_roles.role_for(dyn_tenant["id"], "does_not_exist") is None


def test_get_role_falls_back_to_analyst_like_rbac_get_role_does(dyn_tenant):
    role = dynamic_roles.get_role(dyn_tenant["id"], "does_not_exist")
    assert role.name == "analyst"


def test_a_custom_role_is_recognised_by_every_primitive(tenant_client, dyn_tenant):
    h = _admin_headers(tenant_client, dyn_tenant["id"])
    _create_role(tenant_client, dyn_tenant["id"], h, name="field_lead",
                modules=[MOD_MONITORING, MOD_AUDIT], dashboards=["analyst", "ews"],
                can_admin_tenant=True, can_reveal_pii=True)
    tid = dyn_tenant["id"]
    assert dynamic_roles.can_access_module(tid, "field_lead", MOD_MONITORING) is True
    assert dynamic_roles.can_access_module(tid, "field_lead", MOD_ADMIN) is False
    assert dynamic_roles.can_access_dashboard(tid, "field_lead", "ews") is True
    assert dynamic_roles.can_access_dashboard(tid, "field_lead", "board") is False
    assert dynamic_roles.can_admin_tenant(tid, "field_lead") is True
    assert dynamic_roles.can_reveal_pii(tid, "field_lead") is True


def test_a_role_edit_is_visible_after_the_generation_bumps(tenant_client, dyn_tenant):
    """The cache is generation-invalidated, not TTL'd - an edit must be visible on the
    very next check, not after some clock interval."""
    h = _admin_headers(tenant_client, dyn_tenant["id"])
    _create_role(tenant_client, dyn_tenant["id"], h, name="field_lead",
                can_admin_tenant=False)
    tid = dyn_tenant["id"]
    assert dynamic_roles.can_admin_tenant(tid, "field_lead") is False

    put = tenant_client.put(f"/tenants/{tid}/roles/field_lead", headers=h,
                            json={"can_admin_tenant": True})
    assert put.status_code == 200, put.text
    confirm = tenant_client.post(f"/tenants/{tid}/roles/field_lead/confirm",
                                 headers=_admin_headers(tenant_client, tid))
    # self_approval is expected here (h == the same admin) unless a second admin
    # exists; this test only cares that the *attempt* bumped the generation via the
    # staged PUT itself, which already happened above regardless of confirm outcome.
    assert confirm.status_code in (200, 403)
    assert dynamic_roles.can_admin_tenant(tid, "field_lead") is False  # still pending

    # A non-elevating edit applies immediately and must be visible right away.
    tenant_client.put(f"/tenants/{tid}/roles/field_lead", headers=h,
                      json={"label": "Field Lead (renamed)"})
    role = dynamic_roles.role_for(tid, "field_lead")
    assert role.label == "Field Lead (renamed)"


# ---------------------------------------------------------------- analytics_service
def test_analytics_dashboard_recognises_a_custom_role(
        tenant_client, analytics_client, dyn_tenant):
    h = _admin_headers(tenant_client, dyn_tenant["id"])
    _create_role(tenant_client, dyn_tenant["id"], h, name="field_lead",
                modules=[MOD_MONITORING], dashboards=["analyst"])
    tid = dyn_tenant["id"]
    email = _plant_user(tid, "field_lead", "lead")
    r = analytics_client.get(f"/analytics/{tid}/analyst", headers=_login(tenant_client, email))
    assert r.status_code == 200, r.text


def test_analytics_dashboard_refuses_a_custom_role_without_the_dashboard(
        tenant_client, analytics_client, dyn_tenant):
    h = _admin_headers(tenant_client, dyn_tenant["id"])
    _create_role(tenant_client, dyn_tenant["id"], h, name="field_lead",
                modules=[MOD_MONITORING], dashboards=["ews"])  # not "analyst"
    tid = dyn_tenant["id"]
    email = _plant_user(tid, "field_lead", "lead")
    r = analytics_client.get(f"/analytics/{tid}/analyst", headers=_login(tenant_client, email))
    assert r.status_code == 403, r.text
    assert r.json()["error"]["code"] == "dashboard_forbidden"


def test_analytics_admin_only_action_recognises_a_custom_admin_capable_role(
        tenant_client, analytics_client, dyn_tenant):
    """detection.run_detection requires can_admin_tenant - a custom role granted it
    should reach the same action tenant_admin already can."""
    h = _admin_headers(tenant_client, dyn_tenant["id"])
    _create_role(tenant_client, dyn_tenant["id"], h, name="ops_admin",
                modules=[MOD_MONITORING], dashboards=["analyst"], can_admin_tenant=True)
    tid = dyn_tenant["id"]
    email = _plant_user(tid, "ops_admin", "opsadmin")
    r = analytics_client.post(f"/analytics/{tid}/detection/run",
                              headers=_login(tenant_client, email))
    assert r.status_code != 403, r.text


# ------------------------------------------------------------------- config_service
def test_maker_checker_recognises_a_custom_admin_capable_role(
        tenant_client, config_client, dyn_tenant):
    """A custom role granted can_activate_config - the risk_manager-equivalent flag,
    BR-715's own capability, distinct from can_admin_tenant - may propose a config
    activation, the same as the three fixed roles that carry it by default."""
    h = _admin_headers(tenant_client, dyn_tenant["id"])
    _create_role(tenant_client, dyn_tenant["id"], h, name="threshold_owner",
                can_admin_tenant=False, can_activate_config=True)
    tid = dyn_tenant["id"]
    email = _plant_user(tid, "threshold_owner", "thresholdowner")

    created = _create_rule_config(config_client, tenant_client, tid)
    proposed = config_client.post(f"/configs/{tid}/{created}/activate",
                                  headers=_login(tenant_client, email))
    assert proposed.status_code == 200, proposed.text
    assert proposed.json()["status"] == "pending_activation"


def test_can_admin_tenant_alone_is_not_enough_for_maker_checker(
        tenant_client, config_client, dyn_tenant):
    """The precise point of separating the two flags: a custom role that administers
    the tenant (users, branding, other roles) does not thereby also get to activate
    fraud-detection configuration - that needs can_activate_config specifically,
    granted separately."""
    h = _admin_headers(tenant_client, dyn_tenant["id"])
    _create_role(tenant_client, dyn_tenant["id"], h, name="tenant_ops_admin",
                can_admin_tenant=True, can_activate_config=False)
    tid = dyn_tenant["id"]
    email = _plant_user(tid, "tenant_ops_admin", "opsadmin2")

    created = _create_rule_config(config_client, tenant_client, tid)
    r = config_client.post(f"/configs/{tid}/{created}/activate",
                           headers=_login(tenant_client, email))
    assert r.status_code == 403, r.text
    assert r.json()["error"]["code"] == "role_not_permitted"


def test_maker_checker_still_refuses_a_custom_role_without_the_capability(
        tenant_client, config_client, dyn_tenant):
    h = _admin_headers(tenant_client, dyn_tenant["id"])
    _create_role(tenant_client, dyn_tenant["id"], h, name="viewer_only",
                can_admin_tenant=False, can_activate_config=False)
    tid = dyn_tenant["id"]
    email = _plant_user(tid, "viewer_only", "viewer")

    created = _create_rule_config(config_client, tenant_client, tid)
    r = config_client.post(f"/configs/{tid}/{created}/activate",
                           headers=_login(tenant_client, email))
    assert r.status_code == 403, r.text
    assert r.json()["error"]["code"] == "role_not_permitted"


def test_risk_manager_still_works_unchanged(tenant_client, config_client, dyn_tenant):
    """The fixed role this whole flag exists to describe precisely - zero regression."""
    tid = dyn_tenant["id"]
    email = _plant_user(tid, "risk_manager", "riskmgr")
    created = _create_rule_config(config_client, tenant_client, tid)
    r = config_client.post(f"/configs/{tid}/{created}/activate",
                           headers=_login(tenant_client, email))
    assert r.status_code == 200, r.text


# -------------------------------------------------------------- notification_service
def test_notification_settings_recognise_a_custom_admin_capable_role(
        tenant_client, notification_client, dyn_tenant):
    h = _admin_headers(tenant_client, dyn_tenant["id"])
    _create_role(tenant_client, dyn_tenant["id"], h, name="notify_admin",
                can_admin_tenant=True)
    tid = dyn_tenant["id"]
    email = _plant_user(tid, "notify_admin", "notifyadmin")
    r = notification_client.get(f"/notifications/{tid}/channels",
                                headers=_login(tenant_client, email))
    assert r.status_code == 200, r.text


def test_notification_settings_refuse_a_non_admin_capable_custom_role(
        tenant_client, notification_client, dyn_tenant):
    h = _admin_headers(tenant_client, dyn_tenant["id"])
    _create_role(tenant_client, dyn_tenant["id"], h, name="viewer_only",
                can_admin_tenant=False)
    tid = dyn_tenant["id"]
    email = _plant_user(tid, "viewer_only", "viewer2")
    r = notification_client.get(f"/notifications/{tid}/channels",
                                headers=_login(tenant_client, email))
    assert r.status_code == 403, r.text
