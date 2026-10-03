"""Saved views: server-side, tenant-scoped, owned by a person."""
import pytest


def _save(client, tid, headers, **body):
    return client.post(f"/analytics/{tid}/views", headers=headers,
                       json={"name": "n", "dashboard": "analyst",
                             "query": "rails=UPI&dash=analyst", "shared": False, **body})


def test_save_and_list_round_trip(analytics_client, token_for, tid):
    h = token_for("investigator")
    r = _save(analytics_client, tid, h, name="My morning queue")
    assert r.status_code == 201, r.text
    saved = r.json()
    assert saved["mine"] is True and saved["query"].startswith("rails=UPI")

    listed = analytics_client.get(f"/analytics/{tid}/views", headers=h).json()
    assert any(v["id"] == saved["id"] for v in listed)


def test_saving_the_same_name_overwrites_rather_than_duplicating(analytics_client, token_for, tid):
    h = token_for("supervisor")
    first = _save(analytics_client, tid, h, name="Dup", query="rails=UPI").json()
    second = _save(analytics_client, tid, h, name="Dup", query="rails=RTGS").json()
    assert first["id"] == second["id"], "a second save created a duplicate"
    assert second["query"] == "rails=RTGS"


def test_private_views_are_not_visible_to_others(analytics_client, token_for, tid):
    owner, other = token_for("analyst"), token_for("risk_manager")
    made = _save(analytics_client, tid, owner, name="Private queue", shared=False).json()
    seen = analytics_client.get(f"/analytics/{tid}/views", headers=other).json()
    assert not any(v["id"] == made["id"] for v in seen), "a private view leaked"


def test_shared_views_are_visible_but_marked_not_mine(analytics_client, token_for, tid):
    owner, other = token_for("analyst"), token_for("risk_manager")
    made = _save(analytics_client, tid, owner, name="Team queue", shared=True).json()
    seen = analytics_client.get(f"/analytics/{tid}/views", headers=other).json()
    match = next((v for v in seen if v["id"] == made["id"]), None)
    assert match is not None, "shared view not visible to the team"
    assert match["mine"] is False


def test_only_the_owner_or_a_tenant_admin_may_delete(analytics_client, token_for, tid):
    owner, other = token_for("analyst"), token_for("board")
    made = _save(analytics_client, tid, owner, name="Delete me", shared=True).json()

    # someone else cannot remove it, even though they can see it
    assert analytics_client.delete(f"/analytics/{tid}/views/{made['id']}",
                                   headers=other).status_code == 403
    # a tenant admin can
    assert analytics_client.delete(f"/analytics/{tid}/views/{made['id']}",
                                   headers=token_for("tenant_admin")).status_code == 204


def test_views_are_tenant_scoped(analytics_client, token_for, tid):
    r = analytics_client.get("/analytics/some-other-tenant/views",
                             headers=token_for("analyst"))
    assert r.status_code == 403


def test_deleting_a_missing_view_is_a_clean_404(analytics_client, token_for, tid):
    r = analytics_client.delete(f"/analytics/{tid}/views/does-not-exist",
                                headers=token_for("analyst"))
    assert r.status_code == 404


def test_saving_is_audited(analytics_client, token_for, tid):
    from sqlalchemy import select
    from cp_common import AuditLog, SessionLocal
    _save(analytics_client, tid, token_for("investigator"), name="Audited view")
    db = SessionLocal()
    try:
        actions = {a.action for a in db.scalars(
            select(AuditLog).where(AuditLog.tenant_id == tid))}
    finally:
        db.close()
    assert "view.save" in actions


def test_a_shared_view_never_crosses_into_another_tenant(analytics_client, token_for, tid):
    """The 403 door protects tenant-scoped users, but a platform admin is admitted to
    every tenant - for them only the row filter stands between two banks' saved views.
    "Shared" must mean shared inside one tenant, never across the estate.
    """
    admin = token_for("platform_admin")
    other = "tenant-from-another-bank"
    created = analytics_client.post(
        f"/analytics/{tid}/views", headers=admin,
        json={"name": "Cross-tenant leak probe", "dashboard": "analyst",
              "query": "rails=UPI", "shared": True})
    assert created.status_code == 201, created.text

    # The same caller, admitted to a different tenant, must not see it.
    r = analytics_client.get(f"/analytics/{other}/views", headers=admin)
    assert r.status_code == 200, r.text
    names = [v["name"] for v in r.json()]
    assert "Cross-tenant leak probe" not in names, \
        f"a shared view leaked into another tenant: {names}"

    # ...and it is genuinely visible in the tenant it belongs to, so this is not
    # passing simply because nothing was saved.
    mine = analytics_client.get(f"/analytics/{tid}/views", headers=admin).json()
    assert "Cross-tenant leak probe" in [v["name"] for v in mine]

    analytics_client.delete(f"/analytics/{tid}/views/{created.json()['id']}", headers=admin)


def _save_view(client, tid, headers, name, **kw):
    body = {"name": name, "dashboard": "analyst", "query": "rails=UPI", **kw}
    r = client.post(f"/analytics/{tid}/views", headers=headers, json=body)
    assert r.status_code == 201, r.text
    return r.json()


def _names(client, tid, headers):
    return [v["name"] for v in client.get(f"/analytics/{tid}/views", headers=headers).json()]


def test_role_scoped_share_reaches_only_the_named_roles(analytics_client, token_for, tid):
    """"Shared" must not mean "every L1 analyst sees the principal officer's queue"."""
    _save_view(analytics_client, tid, token_for("principal_officer"), "STR filing queue",
          shared=True, shared_roles=["principal_officer", "supervisor"])
    assert "STR filing queue" in _names(analytics_client, tid, token_for("supervisor"))
    assert "STR filing queue" not in _names(analytics_client, tid, token_for("analyst"))
    assert "STR filing queue" not in _names(analytics_client, tid, token_for("board"))


def test_an_empty_role_list_still_means_the_whole_tenant(analytics_client, token_for, tid):
    """The pre-existing behaviour, and what every migrated row backfills to."""
    _save_view(analytics_client, tid, token_for("risk_manager"), "Everyone view", shared=True)
    for role in ("analyst", "board", "supervisor"):
        assert "Everyone view" in _names(analytics_client, tid, token_for(role))


def test_the_owner_keeps_a_view_they_scoped_away_from_themselves(analytics_client, token_for, tid):
    """Sharing to roles that exclude your own must not hide your own view from you."""
    _save_view(analytics_client, tid, token_for("analyst"), "Handover to the board",
          shared=True, shared_roles=["board"])
    assert "Handover to the board" in _names(analytics_client, tid, token_for("analyst"))


def test_a_role_prefix_does_not_match_a_longer_role(analytics_client, token_for, tid):
    """Guards the LIKE match: ',board,' must not be found inside a future ',board_x,'."""
    from services.analytics_service.app.routes.views import _pack_roles
    assert _pack_roles(tid, ["board"]) == ",board,"
    assert ",board," not in ",board_committee,"


def test_an_unknown_role_is_rejected_rather_than_stored(analytics_client, token_for, tid):
    """A role that is not real would silently make the view visible to no one."""
    r = analytics_client.post(f"/analytics/{tid}/views", headers=token_for("analyst"),
                              json={"name": "Bad audience", "shared": True,
                                    "shared_roles": ["not_a_role"]})
    assert r.status_code == 400, r.text
    assert "Bad audience" not in _names(analytics_client, tid, token_for("analyst"))


def test_audience_is_described_for_the_console(analytics_client, token_for, tid):
    v = _save_view(analytics_client, tid, token_for("analyst"), "Described audience",
              shared=True, shared_roles=["board"])
    assert v["audience"] == "Board / Executive"
    priv = _save_view(analytics_client, tid, token_for("analyst"), "Described private")
    assert priv["audience"] == "Private"


def test_narrowing_an_existing_share_revokes_access(analytics_client, token_for, tid):
    """Re-sharing to a narrower audience must actually take access away."""
    h = token_for("risk_manager")
    _save_view(analytics_client, tid, h, "Narrowing test", shared=True)
    assert "Narrowing test" in _names(analytics_client, tid, token_for("analyst"))
    _save_view(analytics_client, tid, h, "Narrowing test", shared=True, shared_roles=["board"])
    assert "Narrowing test" not in _names(analytics_client, tid, token_for("analyst"))
    assert "Narrowing test" in _names(analytics_client, tid, token_for("board"))


def test_audience_role_list_comes_from_rbac(analytics_client, token_for, tid):
    from cp_common.rbac import ASSIGNABLE_TENANT_ROLES
    r = analytics_client.get(f"/analytics/{tid}/view-audiences", headers=token_for("analyst"))
    assert r.status_code == 200
    assert [x["name"] for x in r.json()] == ASSIGNABLE_TENANT_ROLES
    assert all(x["label"] for x in r.json())
