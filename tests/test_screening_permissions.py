"""Sanctions screening and borrower credit health are gated by permissions, enforced by the
servers.

Both used to check only the tenant, so any signed-in user - a Board member, a data scientist -
could read borrower-level credit data and run sanctions screens. They are now three permissions
on the tenant's own role rows (``sanctions.screen``, ``lane_c.view``, ``lane_c.manage``), and an
administrator can change who holds them.
"""
import inspect

import pytest

from cp_common.rbac import ROLES
from tests.test_dynamic_roles_cross_service import (  # noqa: F401 - dyn_tenant is a fixture
    _admin_headers, _login, _plant_user, dyn_tenant)

SCREEN, VIEW, MANAGE = "sanctions.screen", "lane_c.view", "lane_c.manage"
HOLDERS_OF_ALL = ("tenant_admin", "risk_manager", "principal_officer")
READ_ONLY = ("analyst", "investigator", "supervisor", "rbi_inspector")
NEITHER = ("board", "cro", "data_scientist")


def test_starter_grants_are_as_designed():
    for role in HOLDERS_OF_ALL:
        assert {SCREEN, VIEW, MANAGE} <= set(ROLES[role].permissions), role
    for role in READ_ONLY:
        perms = set(ROLES[role].permissions)
        assert {SCREEN, VIEW} <= perms and MANAGE not in perms, role
    for role in NEITHER:
        assert not ({SCREEN, VIEW, MANAGE} & set(ROLES[role].permissions)), role


def test_every_lane_c_endpoint_is_guarded_by_a_permission():
    """A new Lane C route that forgets the check would be open to the whole tenant again."""
    from services.lane_c_service.app.main import app
    routes = [r for r in app.routes if getattr(r, "path", "").startswith("/lane-c/")]
    assert routes
    for r in routes:
        assert "_authorise(" in inspect.getsource(r.endpoint), r.path


@pytest.mark.parametrize("role", NEITHER)
def test_roles_without_the_grant_are_refused_by_lane_c_and_sanctions(
        lane_c_client, analytics_client, token_for, tid, role):
    h = token_for(role)
    r = lane_c_client.get(f"/lane-c/{tid}/alerts", headers=h)
    assert r.status_code == 403 and r.json()["error"]["code"] == "role_not_permitted", r.text
    r = lane_c_client.get(f"/lane-c/{tid}/borrowers/ACC1/score", headers=h)
    assert r.status_code == 403, r.text
    r = analytics_client.get(f"/analytics/{tid}/ofac-screen", headers=h, params={"name": "Cuba"})
    assert r.status_code == 403, r.text


@pytest.mark.parametrize("role", READ_ONLY)
def test_a_read_only_role_can_look_but_not_change(lane_c_client, analytics_client, token_for, tid, role):
    h = token_for(role)
    assert lane_c_client.get(f"/lane-c/{tid}/alerts", headers=h).status_code == 200
    assert analytics_client.get(f"/analytics/{tid}/ofac-screen", headers=h,
                                params={"name": "Cuba"}).status_code == 200
    r = lane_c_client.post(f"/lane-c/{tid}/alerts/nope/review", headers=h, data={"status": "reviewed"})
    assert r.status_code == 403 and r.json()["error"]["code"] == "role_not_permitted", r.text


@pytest.mark.parametrize("role", HOLDERS_OF_ALL)
def test_a_role_with_manage_passes_the_guard(lane_c_client, token_for, tid, role):
    r = lane_c_client.post(f"/lane-c/{tid}/alerts/nope/review", headers=token_for(role),
                           data={"status": "reviewed"})
    assert r.status_code == 404, r.text       # past the permission check, alert simply does not exist


def test_the_platform_administrator_keeps_access(lane_c_client, analytics_client, token_for, tid):
    h = token_for("platform_admin")
    assert lane_c_client.get(f"/lane-c/{tid}/alerts", headers=h).status_code == 200
    assert analytics_client.get(f"/analytics/{tid}/ofac-screen", headers=h,
                                params={"name": "Cuba"}).status_code == 200


# ------------------------------------------------------- an administrator can change who holds them
def test_removing_the_grant_takes_effect_at_once(tenant_client, lane_c_client, dyn_tenant):
    tid = dyn_tenant["id"]
    admin = _admin_headers(tenant_client, tid)
    analyst = _login(tenant_client, _plant_user(tid, "analyst", "an"))
    assert lane_c_client.get(f"/lane-c/{tid}/alerts", headers=analyst).status_code == 200
    perms = [p for p in ROLES["analyst"].permissions if p != VIEW]
    assert tenant_client.put(f"/tenants/{tid}/roles/analyst", headers=admin,
                             json={"permissions": perms}).status_code == 200
    assert lane_c_client.get(f"/lane-c/{tid}/alerts", headers=analyst).status_code == 403


def test_granting_it_to_board_is_staged_then_confirmed_by_a_second_person(
        tenant_client, lane_c_client, dyn_tenant):
    tid = dyn_tenant["id"]
    admin = _admin_headers(tenant_client, tid)
    board = _login(tenant_client, _plant_user(tid, "board", "bd"))
    assert lane_c_client.get(f"/lane-c/{tid}/alerts", headers=board).status_code == 403
    r = tenant_client.put(f"/tenants/{tid}/roles/board", headers=admin,
                          json={"permissions": ["usage.view", VIEW]})
    assert r.json()["elevation_pending"] is True
    assert lane_c_client.get(f"/lane-c/{tid}/alerts", headers=board).status_code == 403   # not yet
    second = _login(tenant_client, _plant_user(tid, "tenant_admin", "second"))
    assert tenant_client.post(f"/tenants/{tid}/roles/board/confirm", headers=second).status_code == 200
    assert lane_c_client.get(f"/lane-c/{tid}/alerts", headers=board).status_code == 200


def test_me_reports_the_three_grants_the_console_hides_pages_by(tenant_client, dyn_tenant):
    tid = dyn_tenant["id"]
    _admin_headers(tenant_client, tid)
    for role, expected in (("board", set()), ("analyst", {SCREEN, VIEW}),
                           ("risk_manager", {SCREEN, VIEW, MANAGE})):
        me = tenant_client.get("/auth/me", headers=_login(tenant_client, _plant_user(tid, role, "m" + role))).json()
        assert {SCREEN, VIEW, MANAGE} & set(me["permissions"]) == expected, role
