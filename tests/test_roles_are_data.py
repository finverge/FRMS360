"""Roles are data, and the only authority.

What a role may do is read from the tenant's own role rows by every service; no endpoint
decides by role name. These tests change a row and check the next request obeys it, which is
the property that matters: an administrator can reduce or widen a role from the console and it
takes effect at once, for the starter roles as much as for custom ones.
"""
import pytest

from cp_common.permissions import PERMISSIONS
from cp_common.rbac import MOD_MONITORING, ROLES
from tests.test_dynamic_roles_cross_service import (  # noqa: F401 - dyn_tenant is a fixture
    _admin_headers, _create_role, _login, _plant_user, dyn_tenant)


def _roles(tenant_client, tid, headers):
    return {r["name"]: r for r in tenant_client.get(f"/tenants/{tid}/roles", headers=headers).json()}


def _may_load(analytics_client, tid, headers) -> bool:
    r = analytics_client.get(f"/analytics/{tid}/reference", headers=headers)
    assert r.status_code == 200, r.text
    return r.json()["may_load"]


def _second_admin(tenant_client, tid):
    email = _plant_user(tid, "tenant_admin", "second")
    return _login(tenant_client, email)


# ------------------------------------------------------- the starter roles are seeded rows
def test_every_starter_role_is_seeded_with_its_grants(tenant_client, dyn_tenant):
    tid = dyn_tenant["id"]
    rows = _roles(tenant_client, tid, _admin_headers(tenant_client, tid))
    for name, template in ROLES.items():
        if name == "platform_admin":
            continue
        assert name in rows, name
        assert set(rows[name]["permissions"]) == set(template.permissions)
        assert rows[name]["description"] == template.description


def test_the_permission_catalogue_is_offered_to_the_editor(tenant_client, dyn_tenant):
    tid = dyn_tenant["id"]
    body = tenant_client.get(f"/tenants/{tid}/permissions",
                             headers=_admin_headers(tenant_client, tid)).json()
    assert {p["key"] for p in body["permissions"]} == set(PERMISSIONS)
    assert "administration" not in {m["key"] for m in body["modules"]}
    assert "tenant_health" not in {d["key"] for d in body["dashboards"]}


def test_every_case_workflow_action_has_a_permission():
    """The workflow's transitions and the permission catalogue must name the same actions,
    or an action could be un-grantable (or a permission could guard nothing)."""
    from services.analytics_service.app.workflow import TRANSITIONS
    in_workflow = {"case.act." + t.action for t in TRANSITIONS}
    in_catalogue = {k for k in PERMISSIONS if k.startswith("case.act.")}
    assert in_workflow == in_catalogue


# ------------------------------------------------------- an edit is obeyed on the next request
def test_narrowing_a_starter_role_takes_effect_at_once(tenant_client, analytics_client, dyn_tenant):
    tid = dyn_tenant["id"]
    admin = _admin_headers(tenant_client, tid)
    rm = _login(tenant_client, _plant_user(tid, "risk_manager", "rm"))
    assert _may_load(analytics_client, tid, rm) is True
    perms = [p for p in ROLES["risk_manager"].permissions if p != "reference.load"]
    r = tenant_client.put(f"/tenants/{tid}/roles/risk_manager", headers=admin,
                          json={"permissions": perms})
    assert r.status_code == 200, r.text
    assert r.json()["elevation_pending"] is False
    assert _may_load(analytics_client, tid, rm) is False


def test_widening_a_role_is_staged_until_a_second_person_confirms(
        tenant_client, analytics_client, dyn_tenant):
    tid = dyn_tenant["id"]
    admin = _admin_headers(tenant_client, tid)
    analyst = _login(tenant_client, _plant_user(tid, "analyst", "an"))
    assert _may_load(analytics_client, tid, analyst) is False

    r = tenant_client.put(f"/tenants/{tid}/roles/analyst", headers=admin,
                          json={"permissions": ["detection.simulate", "reference.load"]})
    assert r.status_code == 200, r.text
    assert r.json()["elevation_pending"] is True
    assert "reference.load" not in r.json()["permissions"]          # not granted yet
    assert _may_load(analytics_client, tid, analyst) is False

    # the proposer cannot confirm their own elevation
    assert tenant_client.post(f"/tenants/{tid}/roles/analyst/confirm", headers=admin).status_code == 403
    other = _second_admin(tenant_client, tid)
    done = tenant_client.post(f"/tenants/{tid}/roles/analyst/confirm", headers=other)
    assert done.status_code == 200, done.text
    assert "reference.load" in done.json()["permissions"]
    assert _may_load(analytics_client, tid, analyst) is True


def test_an_administrator_has_no_implicit_override(tenant_client, analytics_client, dyn_tenant):
    """The old code let can_admin_tenant pass every gate. Now an action is allowed only if the
    role holds it, administrator or not."""
    tid = dyn_tenant["id"]
    admin = _admin_headers(tenant_client, tid)
    assert _may_load(analytics_client, tid, admin) is True
    perms = [p for p in ROLES["tenant_admin"].permissions if p != "reference.load"]
    assert tenant_client.put(f"/tenants/{tid}/roles/tenant_admin", headers=admin,
                             json={"permissions": perms}).status_code == 200
    assert _may_load(analytics_client, tid, admin) is False


def test_narrowing_a_dashboard_grant_is_refused_by_the_dashboard_at_once(
        tenant_client, analytics_client, dyn_tenant):
    tid = dyn_tenant["id"]
    admin = _admin_headers(tenant_client, tid)
    _create_role(tenant_client, tid, admin, name="field_lead", label="Field Lead",
                 modules=[MOD_MONITORING], dashboards=["analyst", "ews"])
    h = _login(tenant_client, _plant_user(tid, "field_lead", "fl"))
    assert analytics_client.get(f"/analytics/{tid}/ews", headers=h).status_code == 200
    tenant_client.put(f"/tenants/{tid}/roles/field_lead", headers=admin, json={"dashboards": ["analyst"]})
    assert analytics_client.get(f"/analytics/{tid}/ews", headers=h).status_code == 403
    assert analytics_client.get(f"/analytics/{tid}/analyst", headers=h).status_code == 200


# ------------------------------------------------------- guard-rails
def test_an_unknown_permission_is_refused(tenant_client, dyn_tenant):
    tid = dyn_tenant["id"]
    admin = _admin_headers(tenant_client, tid)
    r = tenant_client.put(f"/tenants/{tid}/roles/analyst", headers=admin,
                          json={"permissions": ["does.not.exist"]})
    assert r.status_code == 422, r.text


def test_the_last_working_administrator_cannot_be_removed(tenant_client, dyn_tenant):
    tid = dyn_tenant["id"]
    admin = _admin_headers(tenant_client, tid)   # the only person holding tenant_admin
    r = tenant_client.put(f"/tenants/{tid}/roles/tenant_admin", headers=admin,
                          json={"can_admin_tenant": False})
    assert r.status_code == 422, r.text
    assert r.json()["error"]["code"] == "last_administrator"
    r = tenant_client.delete(f"/tenants/{tid}/roles/tenant_admin", headers=admin)
    assert r.status_code == 409, r.text


def test_administration_may_move_to_another_role_that_someone_holds(tenant_client, dyn_tenant):
    tid = dyn_tenant["id"]
    admin = _admin_headers(tenant_client, tid)
    _create_role(tenant_client, tid, admin, name="ops_lead", label="Ops Lead",
                 modules=[MOD_MONITORING], dashboards=["analyst"], can_admin_tenant=True)
    _plant_user(tid, "ops_lead", "ops")
    # now tenant_admin may lose administration: someone else can still administer
    r = tenant_client.put(f"/tenants/{tid}/roles/tenant_admin", headers=admin,
                          json={"can_admin_tenant": False})
    assert r.status_code == 200, r.text


def test_a_platform_administrator_may_edit_any_tenants_roles(tenant_client, token_for, dyn_tenant):
    tid = dyn_tenant["id"]
    r = tenant_client.put(f"/tenants/{tid}/roles/analyst", headers=token_for("platform_admin"),
                          json={"description": "Edited by platform staff."})
    assert r.status_code == 200, r.text
    assert r.json()["description"] == "Edited by platform staff."


def test_a_role_without_the_administrator_capability_cannot_edit_roles(tenant_client, dyn_tenant):
    tid = dyn_tenant["id"]
    _admin_headers(tenant_client, tid)
    analyst = _login(tenant_client, _plant_user(tid, "analyst", "an2"))
    r = tenant_client.put(f"/tenants/{tid}/roles/analyst", headers=analyst, json={"label": "Hacked"})
    assert r.status_code == 403, r.text


@pytest.mark.parametrize("who", ["analyst", "board"])
def test_me_reports_the_permissions_the_role_holds(tenant_client, dyn_tenant, who):
    tid = dyn_tenant["id"]
    _admin_headers(tenant_client, tid)
    h = _login(tenant_client, _plant_user(tid, who, "x" + who))
    me = tenant_client.get("/auth/me", headers=h).json()
    assert set(me["permissions"]) == set(ROLES[who].permissions)
    assert me["description"] == ROLES[who].description
