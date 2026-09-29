"""Role-based access: modules, dashboards, tenant scope."""
import pytest

from cp_common.rbac import DASHBOARDS, ROLES

TENANT_ROLES = [r for r in ROLES if r != "platform_admin"]


def test_every_dashboard_has_metadata():
    from cp_common.rbac import DASHBOARD_META
    assert set(DASHBOARDS) == set(DASHBOARD_META), "dashboard list and metadata disagree"


def test_every_role_dashboard_is_real():
    for name, role in ROLES.items():
        unknown = set(role.dashboards) - set(DASHBOARDS)
        assert not unknown, f"{name} references unknown dashboards {unknown}"


def test_tenant_health_is_platform_only():
    """Operational telemetry must not reach a bank's own administrator."""
    for name, role in ROLES.items():
        if role.tenant_scoped:
            assert "tenant_health" not in role.dashboards, f"{name} can see tenant_health"


def test_platform_admin_cannot_unmask_customer_data():
    """The outsourcing boundary: running the service never requires seeing customers."""
    assert ROLES["platform_admin"].can_reveal_pii is False


def test_can_activate_config_matches_maker_checkers_own_fixed_set():
    """The single source of truth for who may propose/confirm a configuration
    activation - config_service.maker_checker.ensure_eligible() reads this flag
    directly now, so a drift here is a drift in what that gate actually enforces."""
    granted = {name for name, role in ROLES.items() if role.can_activate_config}
    assert granted == {"tenant_admin", "platform_admin", "risk_manager"}


@pytest.mark.parametrize("role", TENANT_ROLES)
def test_role_reaches_its_own_dashboards(analytics_client, token_for, tid, role):
    for dash in ROLES[role].dashboards:
        r = analytics_client.get(f"/analytics/{tid}/{dash}", headers=token_for(role))
        assert r.status_code == 200, f"{role} blocked from its own {dash}: {r.text[:200]}"


@pytest.mark.parametrize("role", TENANT_ROLES)
def test_role_is_blocked_from_others(analytics_client, token_for, tid, role):
    forbidden = set(DASHBOARDS) - set(ROLES[role].dashboards)
    for dash in sorted(forbidden):
        r = analytics_client.get(f"/analytics/{tid}/{dash}", headers=token_for(role))
        assert r.status_code == 403, f"{role} reached {dash} it should not"


def test_cross_tenant_access_is_refused(analytics_client, token_for):
    r = analytics_client.get("/analytics/some-other-tenant-id/board",
                             headers=token_for("board"))
    assert r.status_code == 403


def test_unauthenticated_is_refused(analytics_client, tid):
    assert analytics_client.get(f"/analytics/{tid}/board").status_code == 401


def test_me_lists_primary_dashboard_first(tenant_client, token_for):
    for role in ("rbi_inspector", "principal_officer", "data_scientist"):
        me = tenant_client.get("/auth/me", headers=token_for(role)).json()
        assert me["dashboards"][0]["key"] == ROLES[role].dashboards[0]
