"""BR-113 (phase 2a): materialising the role catalogue per tenant.

Three things this must prove, in order:
  1. What gets seeded is an exact copy of the catalogue that has always governed
     authorisation - checked against the frozen backup, not just today's rbac.py, so a
     future edit to rbac.py that silently drifts from what was actually backed up is
     caught here rather than discovered in production.
  2. Seeding is idempotent and additive-only - a tenant's own edits are never clobbered.
  3. The application behaves identically whether BR-112's viewer is reading the
     hardcoded catalogue (an old tenant, or one not yet backfilled) or a tenant's own
     materialised rows (a new one) - same response, same tests, unmodified.
"""
import importlib.util
import uuid
from pathlib import Path

import pytest
from sqlalchemy import select

from cp_common.rbac import ASSIGNABLE_TENANT_ROLES, get_role
from services.tenant_service.app.models import Tenant, TenantRole
from services.tenant_service.app.roles import seed_default_roles

BACKUP_PATH = (Path(__file__).resolve().parents[1] / "scripts" / "backups"
              / "rbac_py_pre_br113_20260816.py")


def _load_backup():
    """Import the frozen snapshot as its own module, without touching sys.path -
    it is a backup, not a package, and must never be importable as one by accident."""
    spec = importlib.util.spec_from_file_location("rbac_backup_20260816", BACKUP_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


BACKUP = _load_backup()


def _make_tenant(db, slug: str) -> str:
    tenant = Tenant(slug=slug, legal_name=f"{slug} Ltd.", display_name=slug,
                    status="active")
    db.add(tenant)
    db.commit()
    db.refresh(tenant)
    return tenant.id


# --------------------------------------------------- equivalence with the backup
def test_the_backup_is_a_faithful_snapshot_of_todays_catalogue():
    """Guards the guard: if this fails, the backup is stale and every test below is
    checking seeding against the wrong baseline."""
    assert set(BACKUP.ASSIGNABLE_TENANT_ROLES) == set(ASSIGNABLE_TENANT_ROLES)
    for name in ASSIGNABLE_TENANT_ROLES:
        live, backed_up = get_role(name), BACKUP.get_role(name)
        assert live.label == backed_up.label
        assert set(live.modules) == set(backed_up.modules)
        assert set(live.dashboards) == set(backed_up.dashboards)
        assert live.can_admin_tenant == backed_up.can_admin_tenant
        assert live.can_reveal_pii == backed_up.can_reveal_pii


@pytest.fixture()
def fresh_tenant(tenant_client, seeded):
    """A tenant created directly against the model, bypassing the onboarding saga
    (which calls out to branding-service and config-service over HTTP - a separate
    concern from role seeding). Session-scoped `seeded` guarantees the schema exists."""
    from cp_common import SessionLocal
    db = SessionLocal()
    try:
        tid = _make_tenant(db, f"role-seed-test-{uuid.uuid4().hex[:10]}")
        yield db, tid
    finally:
        db.close()


def test_seeding_creates_exactly_the_backed_up_catalogue(fresh_tenant):
    db, tid = fresh_tenant
    created = seed_default_roles(db, tid)
    db.commit()
    assert {r.name for r in created} == set(BACKUP.ASSIGNABLE_TENANT_ROLES)

    rows = {r.name: r for r in db.scalars(
        select(TenantRole).where(TenantRole.tenant_id == tid))}
    for name in BACKUP.ASSIGNABLE_TENANT_ROLES:
        backed_up = BACKUP.get_role(name)
        row = rows[name]
        assert row.label == backed_up.label
        assert set(row.modules) == set(backed_up.modules)
        assert set(row.dashboards) == set(backed_up.dashboards)
        assert row.can_admin_tenant == backed_up.can_admin_tenant
        assert row.can_reveal_pii == backed_up.can_reveal_pii
        assert row.source == "seeded"


def test_platform_admin_is_never_materialised(fresh_tenant):
    db, tid = fresh_tenant
    seed_default_roles(db, tid)
    db.commit()
    names = set(db.scalars(select(TenantRole.name).where(TenantRole.tenant_id == tid)))
    assert "platform_admin" not in names


# --------------------------------------------------------------- idempotency
def test_seeding_twice_creates_nothing_the_second_time(fresh_tenant):
    db, tid = fresh_tenant
    first = seed_default_roles(db, tid)
    db.commit()
    second = seed_default_roles(db, tid)
    db.commit()
    assert len(first) == len(ASSIGNABLE_TENANT_ROLES)
    assert second == []


def test_seeding_never_overwrites_a_tenants_own_edit(fresh_tenant):
    """A tenant that has since edited a seeded role (once BR-113's write path exists)
    must not have that edit silently reverted by a re-run of seeding or the backfill
    script."""
    db, tid = fresh_tenant
    seed_default_roles(db, tid)
    db.commit()

    edited = db.scalar(select(TenantRole).where(
        TenantRole.tenant_id == tid, TenantRole.name == "analyst"))
    edited.label = "Tier-1 Fraud Reviewer"
    edited.source = "custom"
    db.commit()

    seed_default_roles(db, tid)
    db.commit()

    row = db.scalar(select(TenantRole).where(
        TenantRole.tenant_id == tid, TenantRole.name == "analyst"))
    assert row.label == "Tier-1 Fraud Reviewer"
    assert row.source == "custom"


# ------------------------------------------------- the app behaves identically
def test_the_viewer_serves_dynamic_rows_identically_to_the_hardcoded_fallback(
        tenant_client, token_for, fresh_tenant):
    """Same assertions test_role_catalogue.py already makes against the *hardcoded*
    path (an old tenant, seeded via conftest.py's raw inserts) - repeated here against
    a tenant with real `tenant_roles` rows, to prove the two sources are
    indistinguishable to a caller. If this ever diverges from the hardcoded path,
    BR-112's response contract has silently broken for migrated tenants."""
    db, tid = fresh_tenant
    seed_default_roles(db, tid)
    db.commit()

    r = tenant_client.get(f"/tenants/{tid}/roles", headers=token_for("platform_admin"))
    assert r.status_code == 200, r.text
    by_name = {row["name"]: row for row in r.json()}

    assert set(by_name) == set(ASSIGNABLE_TENANT_ROLES)
    for name in ASSIGNABLE_TENANT_ROLES:
        role = get_role(name)
        row = by_name[name]
        assert row["label"] == role.label
        assert row["can_admin_tenant"] == role.can_admin_tenant
        assert row["can_reveal_pii"] == role.can_reveal_pii
        assert {m["key"] for m in row["modules"]} == set(role.modules)
        assert {d["key"] for d in row["dashboards"]} == set(role.dashboards)
        # A freshly seeded tenant has no users yet under any of these roles.
        assert row["member_count"] == 0


def test_a_tenant_without_materialised_rows_still_uses_the_hardcoded_fallback(
        tenant_client, token_for, tid):
    """`tid` (from the session-scoped `seeded` fixture) predates BR-113 phase 2a and
    has no tenant_roles rows - this is the regression check that matters most: the
    pre-existing, unmodified test_role_catalogue.py suite still passes unchanged, and
    this is the same assertion in miniature."""
    from cp_common import SessionLocal
    db = SessionLocal()
    try:
        existing = db.scalar(select(TenantRole.id).where(TenantRole.tenant_id == tid))
        assert existing is None, "the shared seeded tenant must stay on the fallback path"
    finally:
        db.close()

    r = tenant_client.get(f"/tenants/{tid}/roles", headers=token_for("tenant_admin"))
    assert r.status_code == 200, r.text
    assert {row["name"] for row in r.json()} == set(ASSIGNABLE_TENANT_ROLES)


# --------------------------------------------------------- onboarding wiring
def test_onboarding_materialises_the_new_tenants_roles(monkeypatch):
    """The real saga, not the seeding function in isolation: TenantService.onboard()
    must call seed_default_roles() itself, so every tenant created from here on is on
    the dynamic path without a separate backfill step."""
    from cp_common import SessionLocal
    from services.tenant_service.app import services as svc_module
    from services.tenant_service.app.schemas import TenantCreate

    class _FakeResponse:
        status_code = 200
        def raise_for_status(self): pass
        def json(self): return {}

    monkeypatch.setattr(svc_module.httpx, "put", lambda *a, **k: _FakeResponse())
    monkeypatch.setattr(svc_module.httpx, "post", lambda *a, **k: _FakeResponse())

    db = SessionLocal()
    try:
        payload = TenantCreate(
            slug=f"onboard-role-test-{uuid.uuid4().hex[:10]}",
            legal_name="Onboard Test Ltd.",
            display_name="Onboard Test", admin_email="admin@onboard-test.example",
            admin_password="OnboardPass#2026x")
        tenant = svc_module.TenantService(db).onboard(payload)
        assert tenant.status == "active"

        names = set(db.scalars(
            select(TenantRole.name).where(TenantRole.tenant_id == tenant.id)))
        assert names == set(ASSIGNABLE_TENANT_ROLES)
    finally:
        db.close()
