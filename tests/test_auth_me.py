"""GET /auth/me: what the console renders its navigation and home page from.

It must describe the role the way every enforcing service will treat it - including a
tenant's own custom roles (BR-113), which the static catalogue has never heard of. The
old implementation returned the static ``analyst`` fallback for a custom role, so a custom
role's user was shown an analyst's menu that every endpoint then refused.
"""
import pytest

from cp_common.rbac import MOD_MONITORING, ROLES
# Same tenant/user/role planting helpers the cross-service role tests already use.
from tests.test_dynamic_roles_cross_service import (  # noqa: F401 - dyn_tenant is a fixture
    _admin_headers, _create_role, _login, _plant_user, dyn_tenant)


def test_me_reports_the_capability_flags_of_a_fixed_role(tenant_client, token_for):
    for role in ("platform_admin", "tenant_admin", "analyst", "board", "risk_manager"):
        me = tenant_client.get("/auth/me", headers=token_for(role)).json()
        fixed = ROLES[role]
        assert me["role"] == role
        assert me["can_admin_tenant"] is fixed.can_admin_tenant
        assert me["can_reveal_pii"] is fixed.can_reveal_pii
        assert me["can_activate_config"] is fixed.can_activate_config
        assert [m["key"] for m in me["modules"]] == list(fixed.modules)
        assert [d["key"] for d in me["dashboards"]] == list(fixed.dashboards)


def test_me_carries_the_persona_question_the_home_page_prints(tenant_client, token_for):
    me = tenant_client.get("/auth/me", headers=token_for("analyst")).json()
    first = me["dashboards"][0]
    assert first["key"] == "analyst"
    assert first["label"] and first["persona"] and first["question"]


def test_a_platform_admin_never_gets_pii_reveal(tenant_client, token_for):
    me = tenant_client.get("/auth/me", headers=token_for("platform_admin")).json()
    assert me["can_reveal_pii"] is False
    assert me["tenant_scoped"] is False


def test_me_describes_a_custom_role_by_its_own_grants(tenant_client, dyn_tenant):
    tid = dyn_tenant["id"]
    _create_role(tenant_client, tid, _admin_headers(tenant_client, tid), name="field_lead",
                 label="Field Lead", modules=[MOD_MONITORING], dashboards=["ews", "rfa"],
                 can_reveal_pii=True)
    email = _plant_user(tid, "field_lead", "lead")
    me = tenant_client.get("/auth/me", headers=_login(tenant_client, email)).json()
    assert me["role"] == "field_lead"
    assert me["role_label"] == "Field Lead"
    assert [m["key"] for m in me["modules"]] == [MOD_MONITORING]
    assert [d["key"] for d in me["dashboards"]] == ["ews", "rfa"]
    assert me["can_reveal_pii"] is True
    assert me["can_admin_tenant"] is False


def test_a_role_edit_shows_in_me_straight_away(tenant_client, dyn_tenant):
    tid = dyn_tenant["id"]
    admin = _admin_headers(tenant_client, tid)
    _create_role(tenant_client, tid, admin, name="field_lead", label="Field Lead",
                 dashboards=["ews", "rfa"])
    headers = _login(tenant_client, _plant_user(tid, "field_lead", "lead"))
    me = tenant_client.get("/auth/me", headers=headers).json()
    assert [d["key"] for d in me["dashboards"]] == ["ews", "rfa"]
    # Narrowing a grant is not privilege-raising, so it applies at once.
    r = tenant_client.put(f"/tenants/{tid}/roles/field_lead", headers=admin,
                          json={"label": "Field Lead 2", "dashboards": ["ews"]})
    assert r.status_code == 200, r.text
    me = tenant_client.get("/auth/me", headers=headers).json()
    assert [d["key"] for d in me["dashboards"]] == ["ews"]
    assert me["role_label"] == "Field Lead 2"


def test_a_name_that_resolves_to_nothing_has_no_menu(tenant_client, dyn_tenant):
    """Enforcement treats an unknown role name as no access; the menu must agree rather
    than quietly showing an analyst's."""
    tid = dyn_tenant["id"]
    _create_role(tenant_client, tid, _admin_headers(tenant_client, tid), name="temp_role")
    email = _plant_user(tid, "temp_role", "temp")
    headers = _login(tenant_client, email)
    # The role is then removed out from under the user's still-valid token.
    from cp_common import SessionLocal
    from sqlalchemy import delete
    from services.tenant_service.app.models import TenantRole
    db = SessionLocal()
    try:
        db.execute(delete(TenantRole).where(TenantRole.tenant_id == tid,
                                            TenantRole.name == "temp_role"))
        db.commit()
    finally:
        db.close()
    me = tenant_client.get("/auth/me", headers=headers).json()
    assert me["modules"] == [] and me["dashboards"] == []
    assert me["can_admin_tenant"] is False and me["can_reveal_pii"] is False


@pytest.mark.parametrize("role", ["analyst", "board"])
def test_me_still_requires_authentication(tenant_client, role):
    assert tenant_client.get("/auth/me").status_code == 401
