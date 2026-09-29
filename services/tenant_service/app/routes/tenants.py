"""Presentation tier — tenant registry & onboarding endpoints."""
import httpx
from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from cp_common import tenant_status
from cp_common import (
    AppError,
    Principal,
    get_current_principal,
    get_session,
    record_audit,
    require_role,
    resolve_tenant_scope,
    require_internal_key,
    settings,
)

import secrets

from cp_common import hash_password

from cp_common.rbac import (ASSIGNABLE_TENANT_ROLES, MODULE_META, DASHBOARD_META,
                            dashboards_for, get_role, modules_for)

from .. import roles as roles_mod
from ..models import TenantRole, TenantUser
from ..repositories import TenantRepository
from ..roles import assignable_names
from ..schemas import (RoleCreate, RoleOut, RoleUpdate, TenantCreate, TenantOut,
                       UserInvite, UserInviteResult, UserOut, UserUpdate)
from ..services import TenantService

router = APIRouter(prefix="/tenants", tags=["tenants"])


def require_capability(capability_name: str):
    """Like require_role(), extended to recognise a tenant's own custom roles.

    Every one of the ten fixed roles behaves exactly as require_role() already made
    it behave - can_admin_tenant is what tenant_admin/platform_admin have always
    meant, so nothing changes for them. The only new thing is a role name that is
    NOT one of the ten also being checked, against this tenant's own tenant_roles
    row, instead of being an automatic 403 the way it was before BR-113.

    Shared by both user-management and role-management routes below: managing a
    tenant's users and managing its roles are the same underlying capability.
    """
    def dependency(tenant_id: str, db: Session = Depends(get_session),
                   principal: Principal = Depends(get_current_principal)) -> Principal:
        if principal.is_platform_admin:
            return principal
        resolve_tenant_scope(principal, tenant_id)
        if not roles_mod.capability(db, tenant_id, principal.role, capability_name):
            raise AppError("Insufficient role", 403, "insufficient_role")
        return principal
    return dependency


@router.get("", response_model=list[TenantOut])
def list_tenants(
    db: Session = Depends(get_session),
    principal: Principal = Depends(get_current_principal),
) -> list[TenantOut]:
    """Platform staff see every tenant; a tenant-scoped user sees only their own.

    Returning a one-item list (rather than 403) is what lets an analyst / board /
    supervisor user select their bank and load a dashboard at all.
    """
    repo = TenantRepository(db)
    if principal.is_platform_admin:
        return [TenantOut.model_validate(t) for t in repo.list()]
    if not principal.tenant_id:
        return []
    tenant = repo.get(principal.tenant_id)
    return [TenantOut.model_validate(tenant)] if tenant else []


@router.post("", response_model=TenantOut, status_code=201)
def create_tenant(
    payload: TenantCreate,
    db: Session = Depends(get_session),
    principal: Principal = Depends(require_role("platform_admin")),
) -> TenantOut:
    try:
        tenant = TenantService(db).onboard(payload)
    except AppError as exc:
        record_audit(
            service="tenant-service", action="tenant.onboard", actor=principal.subject,
            actor_role=principal.role, target_type="tenant", target_id=payload.slug,
            status="failure", detail={"error": exc.message},
        )
        raise
    record_audit(
        service="tenant-service", action="tenant.onboard", actor=principal.subject,
        actor_role=principal.role, tenant_id=tenant.id, target_type="tenant",
        target_id=tenant.id, status="success",
        detail={"slug": tenant.slug, "result_status": tenant.status},
    )
    return TenantOut.model_validate(tenant)


@router.post("/self-service", response_model=TenantOut, status_code=201)
def self_service_signup(
    payload: TenantCreate,
    db: Session = Depends(get_session),
) -> TenantOut:
    """BR-109: a prospective bank provisions its own sandbox, no platform_admin needed.

    Deliberately unauthenticated - see ``_is_public`` in the gateway, which admits only
    this one path under the ``tenants`` segment. What keeps that safe is not a login
    wall but what the tenant *is*: a sandbox (``TenantService.onboard(sandbox=True)``),
    capped at one live sandbox per admin email, and permanently unable to submit
    live-labelled traffic (``routes/ingest.py``'s sandbox check) until a platform_admin
    deliberately promotes it. A stranger who signs up gets a fully working integration
    environment and nothing that touches the regulatory fraud record.
    """
    try:
        tenant = TenantService(db).onboard(payload, sandbox=True)
    except AppError as exc:
        record_audit(
            service="tenant-service", action="tenant.self_service_signup", actor=payload.admin_email,
            actor_role="self-service", target_type="tenant", target_id=payload.slug,
            status="failure", detail={"error": exc.message},
        )
        raise
    record_audit(
        service="tenant-service", action="tenant.self_service_signup",
        actor=payload.admin_email, actor_role="self-service", tenant_id=tenant.id,
        target_type="tenant", target_id=tenant.id, status="success",
        detail={"slug": tenant.slug, "result_status": tenant.status},
    )
    return TenantOut.model_validate(tenant)


@router.post("/{tenant_id}/promote", response_model=TenantOut)
def promote_tenant(
    tenant_id: str,
    db: Session = Depends(get_session),
    principal: Principal = Depends(require_role("platform_admin")),
) -> TenantOut:
    """BR-109: the deliberate, human, platform_admin action that turns a self-service
    sandbox into a real tenant. Never automatic, never self-service - see
    ``TenantService.promote``."""
    try:
        tenant = TenantService(db).promote(tenant_id)
    except AppError as exc:
        record_audit(
            service="tenant-service", action="tenant.promote", actor=principal.subject,
            actor_role=principal.role, tenant_id=tenant_id, target_type="tenant",
            target_id=tenant_id, status="failure", detail={"error": exc.message},
        )
        raise
    record_audit(
        service="tenant-service", action="tenant.promote", actor=principal.subject,
        actor_role=principal.role, tenant_id=tenant.id, target_type="tenant",
        target_id=tenant.id, status="success", detail={"slug": tenant.slug},
    )
    return TenantOut.model_validate(tenant)


@router.get("/{tenant_id}", response_model=TenantOut)
def get_tenant(
    tenant_id: str,
    db: Session = Depends(get_session),
    principal: Principal = Depends(get_current_principal),
) -> TenantOut:
    resolve_tenant_scope(principal, tenant_id)  # 403 if cross-tenant
    tenant = TenantRepository(db).get(tenant_id)
    if not tenant:
        raise AppError("Tenant not found", 404, "not_found")
    return TenantOut.model_validate(tenant)


def _transition(action: str, tenant_id: str, db: Session, principal: Principal) -> TenantOut:
    try:
        tenant = TenantService(db).transition(tenant_id, action)
    except AppError as exc:
        record_audit(
            service="tenant-service", action=f"tenant.{action}", actor=principal.subject,
            actor_role=principal.role, tenant_id=tenant_id, target_type="tenant",
            target_id=tenant_id, status="failure", detail={"error": exc.message},
        )
        raise
    record_audit(
        service="tenant-service", action=f"tenant.{action}", actor=principal.subject,
        actor_role=principal.role, tenant_id=tenant.id, target_type="tenant",
        target_id=tenant.id, status="success", detail={"new_status": tenant.status},
    )
    return TenantOut.model_validate(tenant)


@router.post("/{tenant_id}/suspend", response_model=TenantOut)
def suspend_tenant(
    tenant_id: str,
    db: Session = Depends(get_session),
    principal: Principal = Depends(require_role("platform_admin")),
) -> TenantOut:
    return _transition("suspend", tenant_id, db, principal)


@router.post("/{tenant_id}/resume", response_model=TenantOut)
def resume_tenant(
    tenant_id: str,
    db: Session = Depends(get_session),
    principal: Principal = Depends(require_role("platform_admin")),
) -> TenantOut:
    return _transition("resume", tenant_id, db, principal)


@router.post("/{tenant_id}/offboard", response_model=TenantOut)
def offboard_tenant(
    tenant_id: str,
    db: Session = Depends(get_session),
    principal: Principal = Depends(require_role("platform_admin")),
) -> TenantOut:
    return _transition("offboard", tenant_id, db, principal)


@router.get("/{tenant_id}/export")
def export_tenant(
    tenant_id: str,
    db: Session = Depends(get_session),
    principal: Principal = Depends(require_role("platform_admin")),
) -> dict:
    bundle = TenantService(db).export_bundle(tenant_id)
    record_audit(
        service="tenant-service", action="tenant.export", actor=principal.subject,
        actor_role=principal.role, tenant_id=tenant_id, target_type="tenant",
        target_id=tenant_id, status="success",
    )
    return bundle


# ---- User management ----
@router.get("/{tenant_id}/users", response_model=list[UserOut])
def list_users(
    tenant_id: str,
    db: Session = Depends(get_session),
    principal: Principal = Depends(get_current_principal),
) -> list[UserOut]:
    resolve_tenant_scope(principal, tenant_id)
    tenant = TenantRepository(db).get(tenant_id)
    if not tenant:
        raise AppError("Tenant not found", 404, "not_found")
    return [UserOut.model_validate(u) for u in tenant.users]


def _is_admin_capable(db: Session, tenant_id: str, role: str) -> bool:
    return roles_mod.capability(db, tenant_id, role, "can_admin_tenant")


def _sole_admin_guard(db: Session, tenant_id: str, users: list[TenantUser], *,
                      excluding: str, new_role: str | None) -> None:
    """Refuse an update or removal that would leave the tenant with zero users able
    to administer it - platform staff remain a last resort either way, but a bank
    should not have to call the vendor to fix an accident made by opening this up to
    tenant_admin. ``new_role=None`` means removal; a role means reassignment."""
    target = next(u for u in users if u.id == excluding)
    if not _is_admin_capable(db, tenant_id, target.role):
        return  # this user wasn't admin-capable; nothing to protect
    if new_role is not None and _is_admin_capable(db, tenant_id, new_role):
        return  # still admin-capable after the change
    others_capable = any(
        u.id != excluding and _is_admin_capable(db, tenant_id, u.role) for u in users)
    if not others_capable:
        raise AppError(
            "This is the tenant's only user able to administer it; "
            "make someone else admin-capable first", 409, "last_admin")


def _email_invite(*, tenant_id: str, tenant_name: str, to: str, temp_password: str) -> bool:
    """Hand the new user their sign-in directly, through the tenant's own configured
    email channel - the same direct-send path BR-609's scheduled report export already
    uses to reach a recipient with no platform account (notification_service's
    ``POST /internal/channels/send``). Returns False on anything short of a confirmed
    send - no channel configured, the tenant's relay rejecting it, the service being
    unreachable - so the caller always has an honest signal to fall back to showing the
    password on screen instead. Never raises: a bank's own mail relay being down must
    not turn into a 500 on account creation.
    """
    body = (
        f"You've been added as a user of {tenant_name} on Fraud360.\n\n"
        f"Sign in at {settings.console_base_url} with:\n"
        f"  Email:    {to}\n"
        f"  Password: {temp_password}\n\n"
        f"You'll be asked to choose your own password on first sign-in - this one "
        f"works once."
    )
    try:
        r = httpx.post(
            f"{settings.notification_service_url}/internal/channels/send",
            json={"tenant_id": tenant_id, "channel": "email", "to": to,
                 "subject": f"Your Fraud360 access — {tenant_name}", "body": body},
            headers={"x-internal-key": settings.internal_api_key}, timeout=10.0)
        r.raise_for_status()
        return bool(r.json().get("ok"))
    except Exception:  # noqa: BLE001
        return False


@router.post("/{tenant_id}/users", response_model=UserInviteResult, status_code=201)
def invite_user(
    tenant_id: str,
    payload: UserInvite,
    db: Session = Depends(get_session),
    principal: Principal = Depends(require_capability("can_admin_tenant")),
) -> UserInviteResult:
    repo = TenantRepository(db)
    tenant = repo.get(tenant_id)
    if not tenant:
        raise AppError("Tenant not found", 404, "not_found")
    if repo.find_tenant_user(payload.email):
        raise AppError("A user with that email already exists", 409, "user_exists")
    # BR-113: a custom role is assignable once it exists for this tenant, the same as
    # any of the ten fixed roles - never "platform_admin", which is not and cannot
    # become a member of either set. See roles.assignable_names.
    if payload.role not in assignable_names(db, tenant_id):
        raise AppError(f"'{payload.role}' is not a role this tenant has", 422,
                       "unknown_role")

    # Temporary password, single-use by design (must_change_password below). Deliberately
    # NOT written to the audit log either way it is delivered.
    temp_password = secrets.token_urlsafe(12)
    user = repo.add_tenant_user(
        TenantUser(
            tenant_id=tenant_id,
            email=payload.email,
            role=payload.role,
            password_hash=hash_password(temp_password),
            must_change_password=True,  # temp password is single-use by design
        )
    )
    db.commit()
    db.refresh(user)

    # Try the tenant's own mail relay before falling back to showing the password on
    # screen - see _email_invite's docstring for why this never raises on its own.
    emailed = _email_invite(tenant_id=tenant_id, tenant_name=tenant.display_name,
                            to=payload.email, temp_password=temp_password)

    record_audit(
        service="tenant-service", action="user.invite", actor=principal.subject,
        actor_role=principal.role, tenant_id=tenant_id, target_type="user",
        target_id=user.id, status="success",
        detail={"email": payload.email, "role": payload.role, "emailed": emailed},
    )
    return UserInviteResult(
        **UserOut.model_validate(user).model_dump(),
        temp_password=None if emailed else temp_password,
        emailed=emailed,
    )


@router.put("/{tenant_id}/users/{user_id}", response_model=UserOut)
def update_user(
    tenant_id: str,
    user_id: str,
    payload: UserUpdate,
    db: Session = Depends(get_session),
    principal: Principal = Depends(require_capability("can_admin_tenant")),
) -> UserOut:
    """Reassign a user's role.

    For an SSO-federated user this is a stopgap between logins, not a competing
    source of truth - the directory re-syncs the role on their next sign-in
    regardless (routes/sso.py::_complete_sso). It is the only way to change a
    local-password user's role at all, and the only way to change anyone's role
    without waiting for them to log in again.
    """
    repo = TenantRepository(db)
    tenant = repo.get(tenant_id)
    if not tenant:
        raise AppError("Tenant not found", 404, "not_found")
    user = repo.get_tenant_user_by_id(tenant_id, user_id)
    if not user:
        raise AppError("User not found", 404, "not_found")
    if payload.role not in assignable_names(db, tenant_id):
        raise AppError(f"'{payload.role}' is not a role this tenant has", 422,
                       "unknown_role")

    _sole_admin_guard(db, tenant_id, tenant.users, excluding=user_id, new_role=payload.role)

    old_role = user.role
    user.role = payload.role
    db.commit()
    db.refresh(user)
    record_audit(
        service="tenant-service", action="user.update", actor=principal.subject,
        actor_role=principal.role, tenant_id=tenant_id, target_type="user",
        target_id=user_id, status="success",
        detail={"email": user.email, "role_from": old_role, "role_to": payload.role},
    )
    return UserOut.model_validate(user)


@router.delete("/{tenant_id}/users/{user_id}", status_code=204)
def remove_user(
    tenant_id: str,
    user_id: str,
    db: Session = Depends(get_session),
    principal: Principal = Depends(require_capability("can_admin_tenant")),
) -> None:
    repo = TenantRepository(db)
    tenant = repo.get(tenant_id)
    if not tenant:
        raise AppError("Tenant not found", 404, "not_found")
    user = repo.get_tenant_user_by_id(tenant_id, user_id)
    if not user:
        raise AppError("User not found", 404, "not_found")
    # Never leave a tenant with zero users at all...
    if len(tenant.users) <= 1:
        raise AppError("Cannot remove the tenant's only user", 409, "last_user")
    # ...nor, now that this is open to tenant_admin rather than only the vendor's own
    # staff, with users but nobody left who can administer them.
    _sole_admin_guard(db, tenant_id, tenant.users, excluding=user_id, new_role=None)

    email = user.email
    repo.delete_tenant_user(user)
    db.commit()
    record_audit(
        service="tenant-service", action="user.remove", actor=principal.subject,
        actor_role=principal.role, tenant_id=tenant_id, target_type="user",
        target_id=user_id, status="success", detail={"email": email},
    )


# ---- Role catalogue (BR-112 read; BR-113 dynamic source, write path, capability) ----
def _expand(keys: list[str], meta: dict) -> list[dict]:
    """A TenantRole row stores bare module/dashboard keys; rehydrate them with the
    same label/icon/persona metadata modules_for()/dashboards_for() attach to the
    hardcoded catalogue, so the two sources are indistinguishable to a caller."""
    return [{"key": k, **meta[k]} for k in keys if k in meta]


def _role_out(row: TenantRole, member_count: int) -> RoleOut:
    return RoleOut(
        name=row.name, label=row.label,
        modules=_expand(row.modules, MODULE_META),
        dashboards=_expand(row.dashboards, DASHBOARD_META),
        can_admin_tenant=row.can_admin_tenant, can_reveal_pii=row.can_reveal_pii,
        can_activate_config=row.can_activate_config,
        member_count=member_count, source=row.source,
        elevation_pending=bool(row.pending_change),
    )


@router.get("/{tenant_id}/roles", response_model=list[RoleOut])
def list_roles(
    tenant_id: str,
    db: Session = Depends(get_session),
    principal: Principal = Depends(get_current_principal),
) -> list[RoleOut]:
    """What each role can do, and how many of this tenant's own users hold it.

    Read-only by design — see BRD BR-112/BR-113. Same access level as
    ``GET /{tenant_id}/users`` (any authenticated user of this tenant, not just an
    admin) — this is a transparency feature, not a new privilege boundary, so it
    should not be gated more tightly than the user list it complements.

    Source: a tenant onboarded after BR-113 phase 2a has its own materialised
    ``tenant_roles`` rows, read here in preference to the hardcoded catalogue — every
    role the tenant has (the fixed ten, plus any it has since created), not only the
    fixed ten. A tenant onboarded before that (or not yet backfilled) has none, and
    falls back to cp_common.rbac exactly as BR-112 shipped. platform_admin never
    appears from either source: it is cross-tenant and never a bank's own role, the
    same floor BR-108's invite flow already enforces.
    """
    resolve_tenant_scope(principal, tenant_id)
    tenant = TenantRepository(db).get(tenant_id)
    if not tenant:
        raise AppError("Tenant not found", 404, "not_found")
    counts: dict[str, int] = {}
    for u in tenant.users:
        counts[u.role] = counts.get(u.role, 0) + 1

    materialised = list(db.scalars(
        select(TenantRole).where(TenantRole.tenant_id == tenant_id)))
    if materialised:
        return [_role_out(row, counts.get(row.name, 0)) for row in materialised]

    out = []
    for name in ASSIGNABLE_TENANT_ROLES:
        role = get_role(name)
        out.append(RoleOut(
            name=role.name, label=role.label,
            modules=modules_for(name), dashboards=dashboards_for(name),
            can_admin_tenant=role.can_admin_tenant, can_reveal_pii=role.can_reveal_pii,
            can_activate_config=role.can_activate_config,
            member_count=counts.get(name, 0), source=None,
        ))
    return out


@router.post("/{tenant_id}/roles", response_model=RoleOut, status_code=201)
def create_role(
    tenant_id: str,
    payload: RoleCreate,
    db: Session = Depends(get_session),
    principal: Principal = Depends(require_capability("can_admin_tenant")),
) -> RoleOut:
    """BR-113 phase 2b: a tenant defines a role beyond the fixed ten.

    Guarded by ``can_admin_tenant`` — checked dynamically (require_capability), so a
    custom role that itself grants ``can_admin_tenant`` can create further roles, not
    only the seeded ``tenant_admin``/``platform_admin``. Guardrails
    (``roles.validate_grant``) refuse platform-only modules or dashboards regardless
    of who is asking.
    """
    tenant = TenantRepository(db).get(tenant_id)
    if not tenant:
        raise AppError("Tenant not found", 404, "not_found")
    try:
        row = roles_mod.create_role(
            db, tenant_id, name=payload.name, label=payload.label,
            modules=payload.modules, dashboards=payload.dashboards,
            can_admin_tenant=payload.can_admin_tenant,
            can_reveal_pii=payload.can_reveal_pii,
            can_activate_config=payload.can_activate_config)
    except (roles_mod.RoleGuardrailError, roles_mod.RoleConflictError) as exc:
        record_audit(
            service="tenant-service", action="role.create", actor=principal.subject,
            actor_role=principal.role, tenant_id=tenant_id, target_type="role",
            target_id=payload.name, status="failure", detail={"error": str(exc)})
        raise AppError(str(exc), 422, exc.code) from exc
    db.commit()
    roles_mod._publish_role_change()
    db.refresh(row)
    record_audit(
        service="tenant-service", action="role.create", actor=principal.subject,
        actor_role=principal.role, tenant_id=tenant_id, target_type="role",
        target_id=row.name, status="success", detail=roles_mod.snapshot(row))
    return _role_out(row, 0)


@router.put("/{tenant_id}/roles/{name}", response_model=RoleOut)
def update_role(
    tenant_id: str,
    name: str,
    payload: RoleUpdate,
    db: Session = Depends(get_session),
    principal: Principal = Depends(require_capability("can_admin_tenant")),
) -> RoleOut:
    """Edit a role — seeded or custom. Lowering can_admin_tenant/can_reveal_pii/
    can_activate_config (or touching nothing but label/modules/dashboards) applies
    immediately; raising any of the three is staged and needs a different eligible
    actor to confirm — see ``roles.update_role`` and ``POST .../confirm``.
    """
    row = db.scalar(select(TenantRole).where(
        TenantRole.tenant_id == tenant_id, TenantRole.name == name))
    if not row:
        raise AppError("Role not found", 404, "not_found")
    before = roles_mod.snapshot(row)
    try:
        row, elevation_pending = roles_mod.update_role(
            db, row, actor=principal.subject, label=payload.label,
            modules=payload.modules, dashboards=payload.dashboards,
            can_admin_tenant=payload.can_admin_tenant,
            can_reveal_pii=payload.can_reveal_pii,
            can_activate_config=payload.can_activate_config)
    except roles_mod.RoleGuardrailError as exc:
        record_audit(
            service="tenant-service", action="role.update", actor=principal.subject,
            actor_role=principal.role, tenant_id=tenant_id, target_type="role",
            target_id=name, status="failure", detail={"error": str(exc)})
        raise AppError(str(exc), 422, exc.code) from exc
    db.commit()
    roles_mod._publish_role_change()
    db.refresh(row)
    member_count = sum(1 for u in TenantRepository(db).get(tenant_id).users if u.role == name)
    record_audit(
        service="tenant-service",
        action="role.update.staged" if elevation_pending else "role.update",
        actor=principal.subject, actor_role=principal.role, tenant_id=tenant_id,
        target_type="role", target_id=name, status="success",
        # Full before/after snapshot, not a diff - BR-113's audit-based versioning:
        # "what could this role do on date X" is reconstructable from this trail
        # without a separate version table, and it is never rewritten after the fact.
        detail={"before": before, "after": roles_mod.snapshot(row),
               "elevation_pending": elevation_pending})
    return _role_out(row, member_count)


@router.post("/{tenant_id}/roles/{name}/confirm", response_model=RoleOut)
def confirm_role_elevation(
    tenant_id: str,
    name: str,
    db: Session = Depends(get_session),
    principal: Principal = Depends(require_capability("can_admin_tenant")),
) -> RoleOut:
    """A different eligible actor than the one who staged the elevation confirms it.

    Same maker-checker shape as BR-715: the confirming principal is checked against
    ``proposed_by`` at the model layer (roles.confirm_elevation), not just here, so
    there is one place this rule can be forgotten, not two.
    """
    row = db.scalar(select(TenantRole).where(
        TenantRole.tenant_id == tenant_id, TenantRole.name == name))
    if not row:
        raise AppError("Role not found", 404, "not_found")
    before = roles_mod.snapshot(row)
    try:
        row = roles_mod.confirm_elevation(row, principal.subject)
    except roles_mod.SelfConfirmError as exc:
        record_audit(
            service="tenant-service", action="role.elevate.self_approval_refused",
            actor=principal.subject, actor_role=principal.role, tenant_id=tenant_id,
            target_type="role", target_id=name, status="failure")
        raise AppError(str(exc), 403, exc.code) from exc
    except roles_mod.RoleGuardrailError as exc:
        raise AppError(str(exc), 422, exc.code) from exc
    db.commit()
    roles_mod._publish_role_change()
    db.refresh(row)
    record_audit(
        service="tenant-service", action="role.elevate.confirmed",
        actor=principal.subject, actor_role=principal.role, tenant_id=tenant_id,
        target_type="role", target_id=name, status="success",
        detail={"before": before, "after": roles_mod.snapshot(row)})
    member_count = sum(1 for u in TenantRepository(db).get(tenant_id).users if u.role == name)
    return _role_out(row, member_count)


@router.delete("/{tenant_id}/roles/{name}", status_code=204)
def delete_role(
    tenant_id: str,
    name: str,
    db: Session = Depends(get_session),
    principal: Principal = Depends(require_capability("can_admin_tenant")),
) -> None:
    """Only a tenant's own custom role may be deleted — one of the fixed ten cannot,
    since BR-108's invite flow and every historical TenantUser/AuditLog row assumes
    those ten names are always real. Refused while any user still holds the role,
    the same orphan-safety `remove_user` already applies to the tenant's last admin.
    """
    row = db.scalar(select(TenantRole).where(
        TenantRole.tenant_id == tenant_id, TenantRole.name == name))
    if not row:
        raise AppError("Role not found", 404, "not_found")
    if name in ASSIGNABLE_TENANT_ROLES:
        record_audit(
            service="tenant-service", action="role.delete", actor=principal.subject,
            actor_role=principal.role, tenant_id=tenant_id, target_type="role",
            target_id=name, status="failure", detail={"error": "fixed_role"})
        raise AppError("One of the platform's fixed roles cannot be deleted", 409,
                       "fixed_role")
    tenant = TenantRepository(db).get(tenant_id)
    holders = [u.email for u in tenant.users if u.role == name]
    if holders:
        record_audit(
            service="tenant-service", action="role.delete", actor=principal.subject,
            actor_role=principal.role, tenant_id=tenant_id, target_type="role",
            target_id=name, status="failure",
            detail={"error": "role_in_use", "holder_count": len(holders)})
        raise AppError(
            f"{len(holders)} user(s) still hold this role; reassign them first", 409,
            "role_in_use")
    before = roles_mod.snapshot(row)
    db.delete(row)
    db.commit()
    roles_mod._publish_role_change()
    record_audit(
        service="tenant-service", action="role.delete", actor=principal.subject,
        actor_role=principal.role, tenant_id=tenant_id, target_type="role",
        target_id=name, status="success", detail={"before": before})


@router.get("/internal/active")
def internal_active_tenants(
    db: Session = Depends(get_session),
    _: None = Depends(require_internal_key),
) -> dict:
    """Tenant ids that should currently be served, for services that warm caches.

    Only ids and status - no legal names, no user counts. A service warming a rule
    catalogue needs to know which tenants exist, not who they are.
    """
    rows = TenantRepository(db).list()
    served = [t.id for t in rows if t.status not in tenant_status.NOT_SERVED]
    return {"count": len(served), "tenant_ids": served}


@router.get("/internal/{tenant_id}/identity")
def internal_identity(
    tenant_id: str,
    db: Session = Depends(get_session),
    _: None = Depends(require_internal_key),
) -> dict:
    """Who this tenant is, for services that must name it.

    A regulatory return has to state the institution filing it. Analytics holds only a
    tenant id - deliberately, since it has no business in the tenant register - so the
    name is fetched here rather than duplicated into another schema where it would
    quietly go stale after a legal-name change.
    """
    tenant = TenantRepository(db).get(tenant_id)
    if not tenant:
        raise AppError("Tenant not found", 404, "not_found")
    return {"id": tenant.id, "slug": tenant.slug, "legal_name": tenant.legal_name,
            "display_name": tenant.display_name, "entity_type": tenant.entity_type,
            "ucb_tier": tenant.ucb_tier, "status": tenant.status,
            "is_sandbox": tenant.is_sandbox}


@router.get("/internal/{tenant_id}/roles")
def internal_roles(
    tenant_id: str,
    db: Session = Depends(get_session),
    _: None = Depends(require_internal_key),
) -> dict:
    """This tenant's own custom roles, for cp_common.dynamic_roles - the door every
    other service comes through to recognise a BR-113 custom role, since none of them
    has a database grant on this schema.

    Deliberately only the tenant's own rows, not the fixed ten: a caller's role either
    is one of the ten (resolved locally, no request reaches here) or it is a custom
    row, in which case this is the only place that row's definition lives.
    """
    rows = list(db.scalars(select(TenantRole).where(TenantRole.tenant_id == tenant_id)))
    return {"roles": {
        row.name: {"label": row.label, "modules": row.modules,
                  "dashboards": row.dashboards, "can_admin_tenant": row.can_admin_tenant,
                  "can_reveal_pii": row.can_reveal_pii,
                  "can_activate_config": row.can_activate_config}
        for row in rows
    }}
