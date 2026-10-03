"""A tenant's roles: rows in ``tenant.tenant_roles``, and the only authority on what a role may do.

Created from the starter templates in ``cp_common.rbac`` when a tenant is onboarded (and by
the migration that materialised them for existing tenants); from then on the tenant owns them.
An administrator can add a role, change what any role may reach or do, and delete one - the
ten starter roles included, which are ordinary rows with no special standing.

Every service decides from these rows (``cp_common.dynamic_roles``; tenant-service reads them
directly, below). Nothing is decided by role name and nothing falls back to a catalogue in
code: a name that is not a row for the tenant can do nothing. The one role outside this table
is the platform operator (Finverge staff), which is not a bank's role.

Guard-rails that remain, because they protect the tenant from itself:

* a role may only be granted modules, dashboards and permissions that exist, and never the
  platform-only module or Tenant Health;
* raising a privilege (a capability flag or a gated action) is staged and needs a second
  person to confirm; lowering one applies at once;
* an edit or delete that would leave the tenant with no administrator who can sign in is
  refused, and so is deleting a role someone still holds.
"""
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from cp_common.cache import build_backend, verify_backend_supports
from cp_common.rbac import (ALL_MODULES, ASSIGNABLE_TENANT_ROLES, DASHBOARD_META,
                            DASHBOARDS, MOD_ADMIN, MODULE_META, ROLES, Role)
from cp_common.permissions import PERMISSIONS, unknown as unknown_permissions
from cp_common.dynamic_roles import PLATFORM_OPERATOR, platform_operator
from cp_common.settings import settings

# Bumping this is what makes a role change visible to the other five services'
# cp_common.dynamic_roles caches at the same instant, rather than after whatever TTL
# each of them happened to be holding - the same mechanism config_service's
# _publish_config_change() uses for rule/policy changes.
_CACHE_BACKEND = build_backend(settings.cache_backend)
verify_backend_supports(_CACHE_BACKEND, settings.app_replicas)


def _publish_role_change() -> None:
    _CACHE_BACKEND.bump("roles")

from .models import TenantRole, TenantUser

# ------------------------------------------------------------------ guardrails
# A tenant-defined or tenant-edited role can never reach platform-only surface,
# regardless of who is asking - this is the floor BRD BR-113 promises and it is
# enforced here, not only documented. Cross-tenant reach needs no separate guard: a
# TenantRole row's tenant_id is always the path parameter's tenant, never
# client-supplied, so there is no field through which a role could name another
# tenant even if it wanted to.
TENANT_ALLOWED_MODULES = [m for m in ALL_MODULES if m != MOD_ADMIN]
TENANT_ALLOWED_DASHBOARDS = [d for d in DASHBOARDS if d != "tenant_health"]

#: Only the platform operator is reserved: it is not a tenant role. Every other name,
#: including the starter names, is the tenant's to use.
RESERVED_NAMES = {PLATFORM_OPERATOR}


class RoleGuardrailError(ValueError):
    def __init__(self, message: str, code: str = "role_guardrail"):
        super().__init__(message)
        self.code = code


class RoleConflictError(ValueError):
    def __init__(self, message: str, code: str = "role_conflict"):
        super().__init__(message)
        self.code = code


class SelfConfirmError(ValueError):
    code = "self_approval"


def validate_grant(modules: list[str], dashboards: list[str], permissions: list[str] | None = None) -> None:
    """Reject anything a tenant-defined role must never reach.

    Called on every create and every update - an edit that adds a forbidden module is
    exactly as much of a hole as a create that starts with one.
    """
    unknown_m = set(modules) - set(MODULE_META)
    if unknown_m:
        raise RoleGuardrailError(f"unknown module(s): {', '.join(sorted(unknown_m))}")
    bad_p = unknown_permissions(permissions or [])
    if bad_p:
        raise RoleGuardrailError(f"unknown permission(s): {', '.join(sorted(bad_p))}")
    unknown_d = set(dashboards) - set(DASHBOARD_META)
    if unknown_d:
        raise RoleGuardrailError(f"unknown dashboard(s): {', '.join(sorted(unknown_d))}")
    bad_m = set(modules) - set(TENANT_ALLOWED_MODULES)
    if bad_m:
        raise RoleGuardrailError(
            f"'{MOD_ADMIN}' is platform-only and cannot be granted to a tenant "
            f"role: {', '.join(sorted(bad_m))}")
    bad_d = set(dashboards) - set(TENANT_ALLOWED_DASHBOARDS)
    if bad_d:
        raise RoleGuardrailError(
            f"'tenant_health' is platform-operations telemetry and cannot be granted "
            f"to a tenant role: {', '.join(sorted(bad_d))}")


def assignable_names(db: Session, tenant_id: str) -> set[str]:
    """Role names the invite flow and SSO mappings may hand out for this tenant: exactly the
    roles it has. Never the platform operator, which no tenant row can hold."""
    return set(db.scalars(select(TenantRole.name).where(TenantRole.tenant_id == tenant_id)))


# ------------------------------------------------------------------ seeding (2a)
def seed_default_roles(db: Session, tenant_id: str) -> list[TenantRole]:
    """Materialise the fixed catalogue as this tenant's own rows.

    Idempotent by role name: a tenant that already has a row for ``name`` is left
    untouched, so re-running this (onboarding retried, or a backfill script run twice)
    never clobbers a role a tenant has since edited. Returns only the rows it created.
    """
    existing = set(db.scalars(
        select(TenantRole.name).where(TenantRole.tenant_id == tenant_id)))
    created: list[TenantRole] = []
    for name in ASSIGNABLE_TENANT_ROLES:
        if name in existing:
            continue
        role = ROLES[name]
        row = TenantRole(
            tenant_id=tenant_id,
            name=role.name,
            label=role.label,
            modules=list(role.modules),
            dashboards=list(role.dashboards),
            can_admin_tenant=role.can_admin_tenant,
            can_reveal_pii=role.can_reveal_pii,
            can_activate_config=role.can_activate_config,
            permissions=list(role.permissions),
            description=role.description,
            source="seeded",
        )
        db.add(row)
        created.append(row)
    return created


# ------------------------------------------------------------------- write path (2b)
def snapshot(row: TenantRole) -> dict:
    return {"name": row.name, "label": row.label, "modules": list(row.modules),
            "dashboards": list(row.dashboards),
            "can_admin_tenant": row.can_admin_tenant,
            "can_reveal_pii": row.can_reveal_pii,
            "can_activate_config": row.can_activate_config,
            "permissions": list(row.permissions or []), "description": row.description or "",
            "source": row.source}


def create_role(db: Session, tenant_id: str, *, name: str, label: str,
                modules: list[str], dashboards: list[str],
                can_admin_tenant: bool, can_reveal_pii: bool,
                can_activate_config: bool = False, permissions: list[str] | None = None,
                description: str = "") -> TenantRole:
    if name in RESERVED_NAMES:
        raise RoleConflictError(
            f"'{name}' is the platform operator's role name and cannot be used by a "
            f"tenant role", "reserved_name")
    if db.scalar(select(TenantRole.id).where(
            TenantRole.tenant_id == tenant_id, TenantRole.name == name)):
        raise RoleConflictError(f"a role named '{name}' already exists", "name_exists")
    validate_grant(modules, dashboards, permissions)
    # A tenant with no rows yet would otherwise end up with this one role as its whole
    # catalogue. Materialise the starter roles first; it is a no-op for any tenant that has them.
    seed_default_roles(db, tenant_id)
    row = TenantRole(
        tenant_id=tenant_id, name=name, label=label, modules=list(modules),
        dashboards=list(dashboards), can_admin_tenant=can_admin_tenant,
        can_reveal_pii=can_reveal_pii, can_activate_config=can_activate_config,
        permissions=list(permissions or []), description=description, source="custom")
    db.add(row)
    db.flush()
    return row


#: The three flags a role edit stages rather than applies immediately when raised.
_ELEVATABLE = ("can_admin_tenant", "can_reveal_pii", "can_activate_config")


def _admin_members(db: Session, tenant_id: str, admin_roles: set[str]) -> int:
    if not admin_roles:
        return 0
    return db.scalar(select(func.count()).select_from(TenantUser).where(
        TenantUser.tenant_id == tenant_id, TenantUser.role.in_(admin_roles))) or 0


def ensure_admin_remains(db: Session, row: TenantRole, *, admin_after: bool) -> None:
    """Refuse a change that would leave the tenant with nobody who can administer it.

    Counts people, not roles: a role that grants administration but that nobody holds is
    no way back in. Only a change that *removes* the last working administrator is refused;
    a tenant that never had one is not made worse by an unrelated edit.
    """
    rows = list(db.scalars(select(TenantRole).where(TenantRole.tenant_id == row.tenant_id)))
    before = {r.name for r in rows if r.can_admin_tenant}
    after = {r.name for r in rows if (admin_after if r.id == row.id else r.can_admin_tenant)}
    if _admin_members(db, row.tenant_id, before) > 0 and _admin_members(db, row.tenant_id, after) == 0:
        raise RoleGuardrailError(
            "this would leave the tenant with no administrator who can sign in; give "
            "another role administration, and assign someone to it, first", "last_administrator")


def update_role(db: Session, row: TenantRole, *, actor: str, label: str | None = None,
                modules: list[str] | None = None, dashboards: list[str] | None = None,
                can_admin_tenant: bool | None = None,
                can_reveal_pii: bool | None = None,
                can_activate_config: bool | None = None,
                permissions: list[str] | None = None,
                description: str | None = None) -> tuple[TenantRole, bool]:
    """Apply a role edit. Returns (row, elevation_pending).

    A change that *raises* a capability flag or adds a gated action is not applied here - it
    is staged as ``pending_change`` and only takes effect once a different eligible actor
    calls ``confirm_elevation``. A change that only lowers them, or touches nothing but
    label, description, modules or dashboards, applies immediately: reducing what a role can
    do is never a two-person decision, only granting more is - the same asymmetry BR-715's
    maker-checker applies to every other config kind.
    """
    new_modules = row.modules if modules is None else list(modules)
    new_dashboards = row.dashboards if dashboards is None else list(dashboards)
    new_permissions = list(row.permissions or []) if permissions is None else list(permissions)
    validate_grant(new_modules, new_dashboards, new_permissions)

    wanted = {
        "can_admin_tenant": row.can_admin_tenant if can_admin_tenant is None else can_admin_tenant,
        "can_reveal_pii": row.can_reveal_pii if can_reveal_pii is None else can_reveal_pii,
        "can_activate_config": (row.can_activate_config if can_activate_config is None
                                else can_activate_config),
    }
    added = [p for p in new_permissions if p not in (row.permissions or [])]
    elevates = any(wanted[f] and not getattr(row, f) for f in _ELEVATABLE) or bool(added)
    if row.can_admin_tenant and not wanted["can_admin_tenant"]:
        ensure_admin_remains(db, row, admin_after=False)

    if label is not None:
        row.label = label
    if description is not None:
        row.description = description
    row.modules = new_modules
    row.dashboards = new_dashboards
    if row.source == "seeded":
        row.source = "custom"  # no longer a pristine copy of the starter template

    if elevates:
        # What is applied now is the narrower of old and new; the rest waits for a second person.
        row.permissions = [p for p in new_permissions if p in (row.permissions or [])]
        row.pending_change = {**wanted, "permissions": new_permissions}
        row.proposed_by = actor
        return row, True

    for field, value in wanted.items():
        setattr(row, field, value)
    row.permissions = new_permissions
    row.pending_change = None
    row.proposed_by = None
    return row, False


def confirm_elevation(row: TenantRole, actor: str) -> TenantRole:
    if not row.pending_change:
        raise RoleGuardrailError("no pending elevation on this role", "no_pending_change")
    if actor == row.proposed_by:
        raise SelfConfirmError(
            "the person who proposed this elevation cannot also confirm it")
    for field in _ELEVATABLE:
        setattr(row, field, row.pending_change.get(field, getattr(row, field)))
    row.permissions = list(row.pending_change.get("permissions", row.permissions or []))
    row.pending_change = None
    row.proposed_by = None
    return row


# ------------------------------------------------------------- what a role may do
#: Boolean capability flags, as opposed to a module key or a gated action.
_FLAGS = ("can_admin_tenant", "can_reveal_pii", "can_activate_config")


def effective_role(db: Session, tenant_id: str | None, role_name: str) -> Role:
    """The role as every other service will decide it, read straight from the rows.

    Mirrors ``cp_common.dynamic_roles.role_for``: this tenant's row of that name, else a role
    that can do nothing. There is no catalogue in code to fall back on. The platform operator
    is the one name outside the table.
    """
    if role_name == PLATFORM_OPERATOR:
        return platform_operator()
    row = db.scalar(select(TenantRole).where(
        TenantRole.tenant_id == tenant_id, TenantRole.name == role_name)) if tenant_id else None
    if row is None:
        return Role(role_name, role_name, (), (), tenant_scoped=True, can_admin_tenant=False)
    return Role(
        role_name, row.label, tuple(row.modules or ()), tuple(row.dashboards or ()),
        tenant_scoped=True, can_admin_tenant=bool(row.can_admin_tenant),
        can_reveal_pii=bool(row.can_reveal_pii),
        can_activate_config=bool(row.can_activate_config),
        permissions=tuple(row.permissions or ()), description=row.description or "")


def capability(db: Session, tenant_id: str, role_name: str, capability_name: str) -> bool:
    """Whether ``role_name`` grants ``capability_name`` for this tenant.

    ``capability_name`` is one of the three flags, a gated action (``cp_common.permissions``),
    or a module key. Answered from this tenant's role row alone.
    """
    role = effective_role(db, tenant_id, role_name)
    if capability_name in _FLAGS:
        return bool(getattr(role, capability_name))
    if capability_name in PERMISSIONS:
        return capability_name in role.permissions
    return capability_name in role.modules
