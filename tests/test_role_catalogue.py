"""BR-112: a view of the role catalogue and who holds each role.

This file covers the GET path only, which stays exactly as BR-112 shipped it (phase
1 of role flexibility - no permission model of its own, just a report of whatever
cp_common.rbac or, since BR-113, this tenant's own tenant_roles rows grant). The
write path BR-113 phase 2b adds - create/update/confirm/delete - is covered
separately in test_role_writes.py.
"""
from cp_common.rbac import ASSIGNABLE_TENANT_ROLES, ROLES


def test_returns_every_assignable_role_and_no_others(tenant_client, token_for, tid):
    r = tenant_client.get(f"/tenants/{tid}/roles", headers=token_for("tenant_admin"))
    assert r.status_code == 200, r.text
    names = {row["name"] for row in r.json()}
    assert names == set(ASSIGNABLE_TENANT_ROLES)


def test_platform_admin_never_appears_even_for_platform_admin_callers(
        tenant_client, token_for, tid):
    """platform_admin is cross-tenant and never a bank's own role - the same floor
    BR-108's invite flow enforces, held here too even though the caller happens to
    be a platform admin themselves."""
    r = tenant_client.get(f"/tenants/{tid}/roles", headers=token_for("platform_admin"))
    assert r.status_code == 200, r.text
    names = {row["name"] for row in r.json()}
    assert "platform_admin" not in names


def test_each_role_reports_its_real_catalogue_entry(tenant_client, token_for, tid):
    r = tenant_client.get(f"/tenants/{tid}/roles", headers=token_for("tenant_admin"))
    by_name = {row["name"]: row for row in r.json()}
    for name in ASSIGNABLE_TENANT_ROLES:
        role = ROLES[name]  # the starter template the tenant's row was copied from
        row = by_name[name]
        assert row["label"] == role.label
        assert row["can_admin_tenant"] == role.can_admin_tenant
        assert row["can_reveal_pii"] == role.can_reveal_pii
        assert {m["key"] for m in row["modules"]} == set(role.modules)
        assert {d["key"] for d in row["dashboards"]} == set(role.dashboards)


def test_member_counts_reflect_the_seeded_tenant(tenant_client, token_for, tid):
    """The `seeded` fixture plants exactly one user per non-platform role."""
    r = tenant_client.get(f"/tenants/{tid}/roles", headers=token_for("tenant_admin"))
    for row in r.json():
        assert row["member_count"] == 1, row


def test_any_authenticated_tenant_user_may_view_it(tenant_client, token_for, tid):
    """Same access level as GET /{tenant_id}/users - a transparency feature, not a
    new privilege boundary."""
    r = tenant_client.get(f"/tenants/{tid}/roles", headers=token_for("analyst"))
    assert r.status_code == 200, r.text


def test_cross_tenant_access_is_refused(tenant_client, token_for):
    r = tenant_client.get("/tenants/some-other-tenant-id/roles",
                          headers=token_for("tenant_admin"))
    assert r.status_code == 403


def test_unauthenticated_is_refused(tenant_client, tid):
    assert tenant_client.get(f"/tenants/{tid}/roles").status_code == 401


def test_unknown_tenant_is_404_for_platform_admin(tenant_client, token_for):
    r = tenant_client.get("/tenants/does-not-exist/roles",
                          headers=token_for("platform_admin"))
    assert r.status_code == 404


def test_get_has_no_side_effects_and_never_shares_a_route_with_writes(
        tenant_client, token_for, tid):
    """BR-112 shipped this endpoint read-only; BR-113 phase 2b adds real write routes
    at nearby but distinct paths (POST here, PUT/DELETE at .../roles/{name}). This
    guards that the GET path itself was never quietly given write semantics, and that
    an unrouted verb (PATCH - nothing anywhere accepts it) still 404s/405s."""
    r = tenant_client.patch(f"/tenants/{tid}/roles", headers=token_for("tenant_admin"))
    assert r.status_code in (404, 405)
    # PUT/DELETE only exist at .../roles/{name}, never at the bare collection path.
    for method in ("put", "delete"):
        r = getattr(tenant_client, method)(f"/tenants/{tid}/roles",
                                           headers=token_for("tenant_admin"))
        assert r.status_code in (404, 405), f"{method} unexpectedly allowed: {r.status_code}"


def test_the_catalogue_matches_rbac_role_count():
    """Guards against ASSIGNABLE_TENANT_ROLES and ROLES silently drifting apart."""
    assert set(ASSIGNABLE_TENANT_ROLES) == set(ROLES) - {"platform_admin"}
