"""Presentation tier — authentication endpoints.

The login path is deliberately staged. A correct password does not produce a usable token
when the account carries a second factor; it produces a five-minute ``mfa_pending`` token
that can reach nothing except the verification endpoint. That way a stolen password alone
is never sufficient, which is the entire point of the control.
"""
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from cp_common import (
    AppError,
    Principal,
    create_access_token,
    dashboards_for,
    describe_policy,
    generate_backup_codes,
    get_current_principal,
    get_principal_any_scope,
    get_role,
    get_session,
    hash_password,
    modules_for,
    new_totp_secret,
    provisioning_uri,
    qr_svg,
    record_audit,
    settings,
    validate_password,
    verify_password,
    verify_totp,
)

from .. import auth_service as svc
from ..auth_models import UserSession
from ..repositories import TenantRepository
from ..schemas import (
    LoginRequest, MfaActivate, MfaDisable, MfaVerify, PasswordChange, RefreshRequest,
    TokenOut,
)

router = APIRouter(prefix="/auth", tags=["auth"])


def _client(request: Request) -> tuple[str, str]:
    return (request.headers.get("user-agent", ""),
            request.client.host if request.client else "")


def _issue(db: Session, identity: svc.Identity, request: Request, *,
           mfa_satisfied: bool) -> TokenOut:
    """Open a session and mint the pair the client will actually use."""
    ua, ip = _client(request)
    svc.register_success(db, identity)
    _, refresh = svc.open_session(db, identity, mfa_satisfied=mfa_satisfied,
                                  user_agent=ua, ip_addr=ip)
    token = create_access_token(subject=identity.subject, role=identity.role,
                                tenant_id=identity.tenant_id, scope="full")
    record_audit(
        service="tenant-service", action="auth.login", actor=identity.subject,
        actor_role=identity.role, tenant_id=identity.tenant_id, status="success",
        detail={"mfa": mfa_satisfied})
    return TokenOut(access_token=token, role=identity.role,
                    tenant_id=identity.tenant_id, refresh_token=refresh,
                    expires_in=settings.jwt_expire_minutes * 60)


@router.post("/login", response_model=TokenOut)
def login(payload: LoginRequest, request: Request,
          db: Session = Depends(get_session)) -> TokenOut:
    identity = svc.find_identity(db, payload.email)

    # An unknown address and a wrong password are answered identically. Distinguishing
    # them tells an attacker which addresses are worth attacking.
    if identity is None:
        record_audit(
            service="tenant-service", action="auth.login", actor=payload.email,
            status="failure", detail={"reason": "invalid_credentials"})
        raise AppError("Invalid email or password", 401, "invalid_credentials")

    locked = svc.lockout_remaining(identity)
    if locked:
        record_audit(
            service="tenant-service", action="auth.login", actor=identity.subject,
            actor_role=identity.role, tenant_id=identity.tenant_id, status="failure",
            detail={"reason": "locked", "seconds_remaining": locked})
        raise AppError(
            f"Too many failed attempts. Try again in {max(1, locked // 60)} minute(s).",
            423, "account_locked")

    if not svc.password_ok(identity, payload.password):
        now_locked = svc.register_failure(db, identity)
        record_audit(
            service="tenant-service", action="auth.login", actor=identity.subject,
            actor_role=identity.role, tenant_id=identity.tenant_id, status="failure",
            detail={"reason": "invalid_credentials", "locked": bool(now_locked)})
        if now_locked:
            raise AppError(
                f"Too many failed attempts. Try again in "
                f"{settings.lockout_minutes} minute(s).", 423, "account_locked")
        raise AppError("Invalid email or password", 401, "invalid_credentials")

    # Lifecycle gate: suspended/offboarded tenants cannot sign in.
    tenant = svc.tenant_of(db, identity)
    if tenant and tenant.status in ("suspended", "offboarded"):
        record_audit(
            service="tenant-service", action="auth.login", actor=identity.subject,
            actor_role=identity.role, tenant_id=identity.tenant_id, status="failure",
            detail={"reason": f"tenant_{tenant.status}"})
        raise AppError(f"Access disabled: tenant is {tenant.status}", 403,
                       "tenant_inactive")

    # A temporary password may only reach the change-password endpoint. This is checked
    # before the second factor: there is no point proving possession of a device to
    # obtain a token that can do nothing else.
    if bool(identity.record.must_change_password):
        svc.register_success(db, identity)
        token = create_access_token(subject=identity.subject, role=identity.role,
                                    tenant_id=identity.tenant_id,
                                    scope="password_reset")
        record_audit(
            service="tenant-service", action="auth.login", actor=identity.subject,
            actor_role=identity.role, tenant_id=identity.tenant_id, status="success",
            detail={"must_change_password": True})
        return TokenOut(access_token=token, role=identity.role,
                        tenant_id=identity.tenant_id, must_change_password=True)

    required, enrolled = svc.mfa_obligation(db, identity)

    if enrolled:
        # Deliberately NOT clearing the failure count here. The login is not finished,
        # and clearing it would let anyone holding the password re-authenticate in a
        # loop to reset the counter and guess the six digits without limit - which is
        # precisely the attack the second factor exists to stop. The count clears when
        # a session actually opens.
        token = create_access_token(subject=identity.subject, role=identity.role,
                                    tenant_id=identity.tenant_id, scope="mfa_pending")
        return TokenOut(access_token=token, role=identity.role,
                        tenant_id=identity.tenant_id, mfa_required=True)

    if required:
        # Required but not yet set up. The user is let as far as enrolment and no
        # further - refusing outright would leave them with no way to comply.
        token = create_access_token(subject=identity.subject, role=identity.role,
                                    tenant_id=identity.tenant_id, scope="mfa_pending")
        record_audit(
            service="tenant-service", action="auth.mfa_enrolment_required",
            actor=identity.subject, actor_role=identity.role,
            tenant_id=identity.tenant_id, status="success")
        return TokenOut(access_token=token, role=identity.role,
                        tenant_id=identity.tenant_id, mfa_required=True,
                        mfa_enrolment_required=True)

    return _issue(db, identity, request, mfa_satisfied=False)


# ------------------------------------------------------------- second factor
def _identity_or_404(db: Session, principal: Principal) -> svc.Identity:
    identity = svc.find_identity(db, principal.subject)
    if identity is None:
        raise AppError("User not found", 404, "not_found")
    return identity


@router.post("/mfa/enrol")
def enrol_mfa(db: Session = Depends(get_session),
              principal: Principal = Depends(get_principal_any_scope)) -> dict:
    """Begin enrolment: issue a secret and the QR to scan.

    Accepts an ``mfa_pending`` token so a user whose bank has just mandated a second
    factor can enrol without an administrator's help. The secret is stored but not
    enabled - activation needs a code, which proves the device actually works before
    anyone depends on it.
    """
    if principal.scope == "password_reset":
        raise AppError("Change your password first", 403, "password_change_required")
    identity = _identity_or_404(db, principal)

    if identity.record.mfa_enabled:
        raise AppError("A second factor is already active on this account", 409,
                       "mfa_already_enabled")

    secret = new_totp_secret()
    identity.record.mfa_secret = secret
    db.commit()
    uri = provisioning_uri(secret, identity.subject, settings.mfa_issuer)
    record_audit(
        service="tenant-service", action="auth.mfa_enrol_started",
        actor=identity.subject, actor_role=identity.role,
        tenant_id=identity.tenant_id, status="success")
    return {"secret": secret, "otpauth_uri": uri, "qr_svg": qr_svg(uri),
            "issuer": settings.mfa_issuer, "account": identity.subject}


@router.post("/mfa/activate")
def activate_mfa(payload: MfaActivate, db: Session = Depends(get_session),
                 principal: Principal = Depends(get_principal_any_scope)) -> dict:
    """Confirm the device works, switch the factor on, and issue recovery codes."""
    if principal.scope == "password_reset":
        raise AppError("Change your password first", 403, "password_change_required")
    identity = _identity_or_404(db, principal)

    if not identity.record.mfa_secret:
        raise AppError("Start enrolment first", 409, "mfa_not_started")
    if not verify_totp(identity.record.mfa_secret, payload.code):
        record_audit(
            service="tenant-service", action="auth.mfa_activate",
            actor=identity.subject, actor_role=identity.role,
            tenant_id=identity.tenant_id, status="failure")
        raise AppError("That code is not valid. Check the time on your device and try "
                       "the current code.", 401, "invalid_code")

    identity.record.mfa_enabled = True
    identity.record.mfa_enrolled_at = datetime.now(timezone.utc)
    db.commit()

    codes = generate_backup_codes()
    svc.replace_backup_codes(db, identity, codes)
    record_audit(
        service="tenant-service", action="auth.mfa_enabled", actor=identity.subject,
        actor_role=identity.role, tenant_id=identity.tenant_id, status="success")
    # Shown exactly once. Stored hashed, so this response cannot be reproduced.
    return {"enabled": True, "backup_codes": codes,
            "notice": "Store these recovery codes now. Each works once and they cannot "
                      "be shown again."}


@router.post("/mfa/verify", response_model=TokenOut)
def verify_mfa(payload: MfaVerify, request: Request,
               db: Session = Depends(get_session),
               principal: Principal = Depends(get_principal_any_scope)) -> TokenOut:
    """Exchange an mfa_pending token plus a code for a real session."""
    if principal.scope != "mfa_pending":
        raise AppError("No second factor is pending for this session", 400,
                       "no_pending_factor")
    identity = _identity_or_404(db, principal)

    locked = svc.lockout_remaining(identity)
    if locked:
        raise AppError(
            f"Too many failed attempts. Try again in {max(1, locked // 60)} minute(s).",
            423, "account_locked")

    if not identity.record.mfa_enabled:
        raise AppError("Finish setting up your second factor first", 409,
                       "mfa_not_enabled")

    ok = verify_totp(identity.record.mfa_secret, payload.code)
    used_backup = False
    if not ok:
        # A recovery code is the documented fallback, so it is tried before failing.
        ok = used_backup = svc.consume_backup_code(db, identity, payload.code)

    if not ok:
        # Second-factor failures count toward the same lockout as password failures.
        # Treating them separately would give an attacker who has the password a fresh
        # unlimited budget against the six digits that are actually protecting the
        # account.
        now_locked = svc.register_failure(db, identity)
        record_audit(
            service="tenant-service", action="auth.mfa_verify", actor=identity.subject,
            actor_role=identity.role, tenant_id=identity.tenant_id, status="failure",
            detail={"locked": bool(now_locked)})
        if now_locked:
            raise AppError(f"Too many failed attempts. Try again in "
                           f"{settings.lockout_minutes} minute(s).", 423,
                           "account_locked")
        raise AppError("That code is not valid.", 401, "invalid_code")

    record_audit(
        service="tenant-service", action="auth.mfa_verify", actor=identity.subject,
        actor_role=identity.role, tenant_id=identity.tenant_id, status="success",
        detail={"backup_code": used_backup})
    return _issue(db, identity, request, mfa_satisfied=True)


@router.post("/mfa/disable")
def disable_mfa(payload: MfaDisable, db: Session = Depends(get_session),
                principal: Principal = Depends(get_current_principal)) -> dict:
    """Turn the second factor off. Requires the password AND a current code.

    Both, because either alone is exactly the thing the factor exists to defend against:
    a stolen password should not be able to remove it, and neither should a borrowed
    unlocked phone.
    """
    identity = _identity_or_404(db, principal)
    if not identity.record.mfa_enabled:
        raise AppError("No second factor is active", 409, "mfa_not_enabled")
    if not svc.password_ok(identity, payload.password):
        raise AppError("Password is incorrect", 401, "invalid_credentials")
    if not verify_totp(identity.record.mfa_secret, payload.code) and not \
            svc.consume_backup_code(db, identity, payload.code):
        raise AppError("That code is not valid.", 401, "invalid_code")

    required, _ = svc.mfa_obligation(db, identity)
    if required:
        raise AppError(
            "Your organisation requires a second factor for this role, so it cannot be "
            "removed. Re-enrol a new device instead.", 403, "mfa_mandatory")

    identity.record.mfa_enabled = False
    identity.record.mfa_secret = ""
    identity.record.mfa_enrolled_at = None
    db.commit()
    svc.replace_backup_codes(db, identity, [])
    record_audit(
        service="tenant-service", action="auth.mfa_disabled", actor=identity.subject,
        actor_role=identity.role, tenant_id=identity.tenant_id, status="success")
    return {"enabled": False}


@router.get("/mfa/status")
def mfa_status(db: Session = Depends(get_session),
               principal: Principal = Depends(get_principal_any_scope)) -> dict:
    identity = _identity_or_404(db, principal)
    required, enrolled = svc.mfa_obligation(db, identity)
    return {"enabled": enrolled, "required": required,
            "enrolled_at": identity.record.mfa_enrolled_at,
            "backup_codes_remaining": svc.remaining_backup_codes(db, identity)}


# ----------------------------------------------------------------- sessions
@router.post("/refresh", response_model=TokenOut)
def refresh(payload: RefreshRequest, request: Request,
            db: Session = Depends(get_session)) -> TokenOut:
    """Exchange a refresh token for a new access token, rotating the refresh token."""
    ua, ip = _client(request)
    try:
        session, raw = svc.rotate_session(db, payload.refresh_token,
                                          user_agent=ua, ip_addr=ip)
    except svc.RefreshRefused as exc:
        raise AppError(exc.message, 401, exc.code)

    token = create_access_token(subject=session.subject, role=session.role,
                                tenant_id=session.tenant_id, scope="full")
    return TokenOut(access_token=token, role=session.role, tenant_id=session.tenant_id,
                    refresh_token=raw,
                    expires_in=settings.jwt_expire_minutes * 60)


@router.post("/logout")
def logout(payload: RefreshRequest, db: Session = Depends(get_session)) -> dict:
    """End this session. Deliberately unauthenticated beyond the token itself: a client
    whose access token has already expired must still be able to close its session."""
    from cp_common import hash_refresh_token

    session = db.scalar(select(UserSession).where(
        UserSession.refresh_hash == hash_refresh_token(payload.refresh_token)))
    if session and session.revoked_at is None:
        session.revoked_at = datetime.now(timezone.utc)
        session.revoked_reason = "logout"
        db.commit()
        record_audit(
            service="tenant-service", action="auth.logout", actor=session.subject,
            actor_role=session.role, tenant_id=session.tenant_id, status="success")
    return {"ended": True}


@router.post("/logout-all")
def logout_all(db: Session = Depends(get_session),
               principal: Principal = Depends(get_current_principal)) -> dict:
    identity = _identity_or_404(db, principal)
    n = svc.revoke_all_for_user(db, identity.kind, identity.user_id, "logout_all")
    record_audit(
        service="tenant-service", action="auth.logout_all", actor=identity.subject,
        actor_role=identity.role, tenant_id=identity.tenant_id, status="success",
        detail={"sessions_ended": n})
    return {"sessions_ended": n}


@router.get("/sessions")
def list_sessions(db: Session = Depends(get_session),
                  principal: Principal = Depends(get_current_principal)) -> list[dict]:
    """Where this account is signed in. A user who cannot see their own sessions cannot
    notice one they did not start."""
    identity = _identity_or_404(db, principal)
    rows = db.scalars(select(UserSession).where(
        UserSession.user_kind == identity.kind,
        UserSession.user_id == identity.user_id,
        UserSession.revoked_at.is_(None)).order_by(
            UserSession.last_used_at.desc())).all()
    return [{"id": r.id, "issued_at": r.issued_at, "last_used_at": r.last_used_at,
             "expires_at": r.expires_at, "ip_addr": r.ip_addr,
             "user_agent": r.user_agent[:120], "mfa": r.mfa_satisfied}
            for r in rows]


@router.get("/me", tags=["auth"])
def whoami(
    principal: Principal = Depends(get_principal_any_scope),
) -> dict:
    """Identity plus the modules/dashboards this role may use.

    The console renders its navigation from this, so UI and API cannot disagree about
    who sees what. It is a convenience, not the control: every endpoint re-checks.
    """
    role = get_role(principal.role)
    return {
        "subject": principal.subject,
        "role": role.name,
        "role_label": role.label,
        "tenant_id": principal.tenant_id,
        "tenant_scoped": role.tenant_scoped,
        "can_admin_tenant": role.can_admin_tenant,
        "must_change_password": principal.scope == "password_reset",
        "modules": modules_for(role.name),
        "dashboards": dashboards_for(role.name),
    }


@router.get("/password-policy", tags=["auth"])
def password_policy() -> dict:
    """Public: lets the console show the same rules the API enforces."""
    return {"policy": describe_policy()}


@router.post("/change-password", response_model=TokenOut)
def change_password(
    payload: PasswordChange,
    db: Session = Depends(get_session),
    # Accepts password_reset-scoped tokens — this is the one endpoint they may call.
    principal: Principal = Depends(get_principal_any_scope),
) -> TokenOut:
    repo = TenantRepository(db)
    platform = repo.find_platform_user(principal.subject)
    user = platform or repo.find_tenant_user(principal.subject)
    if not user:
        raise AppError("User not found", 404, "not_found")

    if not verify_password(payload.current_password, user.password_hash):
        record_audit(
            service="tenant-service", action="auth.change_password", actor=principal.subject,
            actor_role=principal.role, tenant_id=getattr(user, "tenant_id", None),
            status="failure", detail={"reason": "current_password_incorrect"},
        )
        raise AppError("Current password is incorrect", 401, "invalid_credentials")

    if payload.new_password == payload.current_password:
        raise AppError(
            "New password must differ from the current one", 400, "password_unchanged"
        )

    problems = validate_password(payload.new_password, email=user.email)
    if problems:
        record_audit(
            service="tenant-service", action="auth.change_password", actor=principal.subject,
            actor_role=principal.role, tenant_id=getattr(user, "tenant_id", None),
            status="failure", detail={"reason": "policy_violation"},
        )
        raise AppError("Password " + "; ".join(problems), 400, "weak_password")

    user.password_hash = hash_password(payload.new_password)
    user.must_change_password = False
    user.password_changed_at = datetime.now(timezone.utc)
    db.commit()

    tenant_id = getattr(user, "tenant_id", None)
    record_audit(
        service="tenant-service", action="auth.change_password", actor=principal.subject,
        actor_role=principal.role, tenant_id=tenant_id, status="success",
    )
    # Issue a fresh full-scope token so the client can continue without re-login.
    token = create_access_token(
        subject=user.email, role=user.role, tenant_id=tenant_id, scope="full"
    )
    return TokenOut(
        access_token=token, role=user.role, tenant_id=tenant_id, must_change_password=False
    )
