"""BR-113: a tenant's own materialised, and now editable, role catalogue.

Seeded from cp_common.rbac - the platform's long-standing, hardcoded catalogue - so a
tenant's ``tenant_roles`` rows start as an exact copy of what every tenant already had
implicitly, before this table existed (phase 2a). This module now also carries phase
2b: the write path, guardrails, elevation confirmation and capability check.

The other five services - analytics, config, decision, ingestion, notification - now
also recognise a custom role, via ``cp_common.dynamic_roles``: they have no database
grant on this schema (schema-per-service, HLD AD-03), so they fetch this tenant's
custom-role catalogue over HTTP from ``routes.tenants.internal_roles`` and cache it
against the "roles" generation this module bumps below. That covers module/dashboard
access and all three capability flags - ``can_admin_tenant``, ``can_reveal_pii``,
``can_activate_config`` (the risk_manager-equivalent grant BR-715's maker-checker
runs on) - everywhere. A smaller number of call sites still gate an action behind a
named list of fixed roles for reasons narrower than any of these three flags
(analytics_service's ``FILING_ROLES``/``CTR_ROLES``/etc., which mix in roles this
platform has no dedicated flag for at all) - extending those needs capability flags
this platform does not have yet, and is real, separately-scoped work.

What this still does NOT do:

  * Renaming a role is not supported - ``name`` is the stable identifier every
    string-typed reference already depends on (TenantUser.role, AuditLog.actor_role,
    JWT claims), and changing it would mean migrating all of them atomically. Editing
    everything else about a role is supported.

See scripts/backups/rbac_py_pre_br113_20260816.py for the frozen snapshot the seeding
logic is tested against.
"""
from sqlalchemy import select
from sqlalchemy.orm import Session

from cp_common.cache import build_backend, verify_backend_supports
from cp_common.rbac import (ALL_MODULES, ASSIGNABLE_TENANT_ROLES, DASHBOARD_META,
                            DASHBOARDS, MOD_ADMIN, MODULE_META, get_role)
from cp_common.settings import settings

# Bumping this is what makes a role change visible to the other five services'
# cp_common.dynamic_roles caches at the same instant, rather than after whatever TTL
# each of them happened to be holding - the same mechanism config_service's
# _publish_config_change() uses for rule/policy changes.
_CACHE_BACKEND = build_backend(settings.cache_backend)
verify_backend_supports(_CACHE_BACKEND, settings.app_replicas)


def _publish_role_change() -> None:
    _CACHE_BACKEND.bump("roles")

from .models import TenantRole

# ------------------------------------------------------------------ guardrails
# A tenant-defined or tenant-edited role can never reach platform-only surface,
# regardless of who is asking - this is the floor BRD BR-113 promises and it is
# enforced here, not only documented. Cross-tenant reach needs no separate guard: a
# TenantRole row's tenant_id is always the path parameter's tenant, never
# client-supplied, so there is no field through which a role could name another
# tenant even if it wanted to.
TENANT_ALLOWED_MODULES = [m for m in ALL_MODULES if m != MOD_ADMIN]
TENANT_ALLOWED_DASHBOARDS = [d for d in DASHBOARDS if d != "tenant_health"]

RESERVED_NAMES = set(ASSIGNABLE_TENANT_ROLES) | {"platform_admin"}


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


def validate_grant(modules: list[str], dashboards: list[str]) -> None:
    """Reject anything a tenant-defined role must never reach.

    Called on every create and every update - an edit that adds a forbidden module is
    exactly as much of a hole as a create that starts with one.
    """
    unknown_m = set(modules) - set(MODULE_META)
    if unknown_m:
        raise RoleGuardrailError(f"unknown module(s): {', '.join(sorted(unknown_m))}")
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
    """Role names BR-108's invite flow may hand out for this tenant: the ten fixed
    roles (so an old tenant with no tenant_roles rows yet is unaffected) plus any
    role - seeded or custom - actually materialised here. Never "platform_admin";
    that name is excluded from ASSIGNABLE_TENANT_ROLES already and no tenant_roles
    row can ever hold it (seed_default_roles never writes it, create_role refuses it
    via RESERVED_NAMES)."""
    materialised = set(db.scalars(
        select(TenantRole.name).where(TenantRole.tenant_id == tenant_id)))
    return set(ASSIGNABLE_TENANT_ROLES) | materialised


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
        role = get_role(name)
        row = TenantRole(
            tenant_id=tenant_id,
            name=role.name,
            label=role.label,
            modules=list(role.modules),
            dashboards=list(role.dashboards),
            can_admin_tenant=role.can_admin_tenant,
            can_reveal_pii=role.can_reveal_pii,
            can_activate_config=role.can_activate_config,
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
            "can_activate_config": row.can_activate_config, "source": row.source}


def create_role(db: Session, tenant_id: str, *, name: str, label: str,
                modules: list[str], dashboards: list[str],
                can_admin_tenant: bool, can_reveal_pii: bool,
                can_activate_config: bool = False) -> TenantRole:
    if name in RESERVED_NAMES:
        raise RoleConflictError(
            f"'{name}' is one of the platform's own role names and cannot be reused "
            f"by a tenant-defined role", "reserved_name")
    if db.scalar(select(TenantRole.id).where(
            TenantRole.tenant_id == tenant_id, TenantRole.name == name)):
        raise RoleConflictError(f"a role named '{name}' already exists", "name_exists")
    validate_grant(modules, dashboards)
    row = TenantRole(
        tenant_id=tenant_id, name=name, label=label, modules=list(modules),
        dashboards=list(dashboards), can_admin_tenant=can_admin_tenant,
        can_reveal_pii=can_reveal_pii, can_activate_config=can_activate_config,
        source="custom")
    db.add(row)
    db.flush()
    return row


#: The three flags a role edit stages rather than applies immediately when raised.
_ELEVATABLE = ("can_admin_tenant", "can_reveal_pii", "can_activate_config")


def update_role(db: Session, row: TenantRole, *, actor: str, label: str | None = None,
                modules: list[str] | None = None, dashboards: list[str] | None = None,
                can_admin_tenant: bool | None = None,
                can_reveal_pii: bool | None = None,
                can_activate_config: bool | None = None) -> tuple[TenantRole, bool]:
    """Apply a role edit. Returns (row, elevation_pending).

    A change that *raises* any of the three capability flags from False to True is
    not applied here - it is staged as ``pending_change`` and only takes effect once
    a different eligible actor calls ``confirm_elevation``. A change that only lowers
    them, or touches nothing but label/modules/dashboards, applies immediately:
    reducing what a role can reach is never a two-person decision, only granting more
    is - the same asymmetry BR-715's maker-checker already applies to every other
    config kind.
    """
    new_modules = row.modules if modules is None else list(modules)
    new_dashboards = row.dashboards if dashboards is None else list(dashboards)
    validate_grant(new_modules, new_dashboards)

    wanted = {
        "can_admin_tenant": row.can_admin_tenant if can_admin_tenant is None else can_admin_tenant,
        "can_reveal_pii": row.can_reveal_pii if can_reveal_pii is None else can_reveal_pii,
        "can_activate_config": (row.can_activate_config if can_activate_config is None
                                else can_activate_config),
    }
    elevates = any(wanted[f] and not getattr(row, f) for f in _ELEVATABLE)

    if label is not None:
        row.label = label
    row.modules = new_modules
    row.dashboards = new_dashboards
    if row.source == "seeded":
        row.source = "custom"  # no longer a pristine copy of the platform catalogue

    if elevates:
        row.pending_change = wanted
        row.proposed_by = actor
        return row, True

    for field, value in wanted.items():
        setattr(row, field, value)
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
    row.pending_change = None
    row.proposed_by = None
    return row


# ------------------------------------------------------------- capability check (2c)
#: Boolean capability flags, as opposed to a module key checked for membership.
_FLAGS = ("can_admin_tenant", "can_reveal_pii", "can_activate_config")


def capability(db: Session, tenant_id: str, role_name: str, capability_name: str) -> bool:
    """Whether ``role_name`` grants ``capability_name`` for this tenant.

    ``capability_name`` is one of the three flags in ``_FLAGS``, or a module key (see
    cp_common.rbac.ALL_MODULES).

    Checks the fixed catalogue first - a seeded role's behaviour is byte-identical to
    the pre-BR-113 hardcoded check, zero regression risk. Only a name that is NOT one
    of the platform's own ten falls through to this tenant's own ``tenant_roles`` row,
    so a custom role is capability-checked, not merely stored.

    This is deliberately scoped to tenant-service itself - see the module docstring
    for why the other five services cannot cheaply do the same yet.
    """
    if role_name in ASSIGNABLE_TENANT_ROLES:
        role = get_role(role_name)
        if capability_name in _FLAGS:
            return getattr(role, capability_name)
        return capability_name in role.modules
    row = db.scalar(select(TenantRole).where(
        TenantRole.tenant_id == tenant_id, TenantRole.name == role_name))
    if not row:
        return False
    if capability_name in _FLAGS:
        return bool(getattr(row, capability_name))
    return capability_name in (row.modules or [])
