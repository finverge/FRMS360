"""Authentication behaviour, separated from the HTTP layer.

The routes decide status codes and shapes; this decides who gets in. Keeping them apart is
what makes the lockout and rotation rules testable without a client, and it stops the
"is this user allowed" logic from being duplicated across the four endpoints that need it.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from cp_common import (
    hash_backup_code, hash_refresh_token, is_expired, mfa_required_for,
    new_refresh_token, record_audit, settings, verify_password,
)

from .auth_models import MfaBackupCode, UserSession
from .models import PlatformUser, Tenant, TenantUser


class AuthOutcome:
    OK = "ok"
    INVALID = "invalid_credentials"
    LOCKED = "account_locked"
    TENANT_INACTIVE = "tenant_inactive"
    MFA_REQUIRED = "mfa_required"
    MFA_ENROLMENT_REQUIRED = "mfa_enrolment_required"
    MUST_CHANGE_PASSWORD = "must_change_password"


@dataclass
class Identity:
    """Whichever kind of user signed in, reduced to what the rest of auth needs."""
    user_id: str
    kind: str                    # platform | tenant
    subject: str
    role: str
    tenant_id: str | None
    record: object               # the ORM row, for mutation


def find_identity(db: Session, email: str) -> Identity | None:
    platform = db.scalar(select(PlatformUser).where(PlatformUser.email == email))
    if platform:
        return Identity(platform.id, "platform", platform.email, platform.role, None,
                        platform)
    user = db.scalar(select(TenantUser).where(TenantUser.email == email))
    if user:
        return Identity(user.id, "tenant", user.email, user.role, user.tenant_id, user)
    return None


def lockout_remaining(identity: Identity) -> int:
    """Seconds until the account unlocks; 0 when it is not locked."""
    until = getattr(identity.record, "locked_until", None)
    if until is None:
        return 0
    if until.tzinfo is None:
        until = until.replace(tzinfo=timezone.utc)
    delta = (until - datetime.now(timezone.utc)).total_seconds()
    return max(0, int(delta))


def register_failure(db: Session, identity: Identity) -> int:
    """Count a failed attempt and lock the account if it crosses the threshold.

    Returns the remaining lockout in seconds (0 if still unlocked).
    """
    rec = identity.record
    rec.failed_attempts = (rec.failed_attempts or 0) + 1
    locked = 0
    if rec.failed_attempts >= settings.max_failed_attempts:
        rec.locked_until = datetime.now(timezone.utc) + timedelta(
            minutes=settings.lockout_minutes)
        rec.failed_attempts = 0          # the lock replaces the count
        locked = settings.lockout_minutes * 60
        record_audit(
            service="tenant-service", action="auth.lockout", actor=identity.subject,
            actor_role=identity.role, tenant_id=identity.tenant_id, status="failure",
            detail={"minutes": settings.lockout_minutes})
    db.commit()
    return locked


def clear_failures(db: Session, identity: Identity) -> None:
    """Called the moment the password verifies.

    Separate from ``register_success`` on purpose: a correct password ends the guessing
    streak even when the login is not finished, because the streak is what the lockout
    is counting. Leaving it uncleared until after the second factor meant a user who
    mistyped twice and then signed in properly stayed two attempts from a lockout.
    """
    rec = identity.record
    rec.failed_attempts = 0
    rec.locked_until = None
    db.commit()


def register_success(db: Session, identity: Identity) -> None:
    """Called when a session actually opens - the login is complete."""
    rec = identity.record
    rec.failed_attempts = 0
    rec.locked_until = None
    rec.last_login_at = datetime.now(timezone.utc)
    db.commit()


def tenant_of(db: Session, identity: Identity) -> Tenant | None:
    if not identity.tenant_id:
        return None
    return db.get(Tenant, identity.tenant_id)


def mfa_obligation(db: Session, identity: Identity) -> tuple[bool, bool]:
    """(second factor required, already enrolled).

    Platform staff are always required to hold one: they can reach every tenant, and an
    outsourcing review will ask about exactly that account.
    """
    enrolled = bool(getattr(identity.record, "mfa_enabled", False))
    if identity.kind == "platform":
        return True, enrolled
    tenant = tenant_of(db, identity)
    policy = getattr(tenant, "mfa_policy", "privileged") if tenant else "privileged"
    return mfa_required_for(policy, identity.role), enrolled


# --------------------------------------------------------------------- sessions
def open_session(db: Session, identity: Identity, *, mfa_satisfied: bool,
                 user_agent: str = "", ip_addr: str = "") -> tuple[UserSession, str]:
    """Start a session and return it with the raw refresh token (shown once)."""
    import uuid

    raw = new_refresh_token()
    session = UserSession(
        user_id=identity.user_id, user_kind=identity.kind, tenant_id=identity.tenant_id,
        subject=identity.subject, role=identity.role,
        refresh_hash=hash_refresh_token(raw), family_id=str(uuid.uuid4()),
        expires_at=datetime.now(timezone.utc) + timedelta(days=settings.refresh_token_days),
        user_agent=(user_agent or "")[:255], ip_addr=(ip_addr or "")[:45],
        mfa_satisfied=mfa_satisfied)
    db.add(session)
    db.commit()
    return session, raw


def revoke_family(db: Session, family_id: str, reason: str) -> int:
    """End every session in a rotation chain."""
    rows = db.scalars(select(UserSession).where(
        UserSession.family_id == family_id, UserSession.revoked_at.is_(None))).all()
    now = datetime.now(timezone.utc)
    for r in rows:
        r.revoked_at, r.revoked_reason = now, reason
    db.commit()
    return len(rows)


def revoke_all_for_user(db: Session, user_kind: str, user_id: str, reason: str) -> int:
    rows = db.scalars(select(UserSession).where(
        UserSession.user_kind == user_kind, UserSession.user_id == user_id,
        UserSession.revoked_at.is_(None))).all()
    now = datetime.now(timezone.utc)
    for r in rows:
        r.revoked_at, r.revoked_reason = now, reason
    db.commit()
    return len(rows)


class RefreshRefused(Exception):
    def __init__(self, message: str, code: str):
        super().__init__(message)
        self.message, self.code = message, code


def rotate_session(db: Session, raw_token: str, *, user_agent: str = "",
                   ip_addr: str = "") -> tuple[UserSession, str]:
    """Exchange a refresh token for a new one, retiring the old.

    A token presented after it has already been exchanged means two parties hold it. The
    honest reading is theft, and serving either party would be worse than serving neither,
    so the entire rotation chain is ended and both must sign in again.
    """
    existing = db.scalar(select(UserSession).where(
        UserSession.refresh_hash == hash_refresh_token(raw_token)))
    if existing is None:
        raise RefreshRefused("Session not recognised. Sign in again.", "unknown_session")

    if existing.revoked_at is not None:
        revoked = revoke_family(db, existing.family_id, "token_reuse")
        record_audit(
            service="tenant-service", action="auth.refresh_reuse",
            actor=existing.subject, actor_role=existing.role,
            tenant_id=existing.tenant_id, status="failure",
            detail={"sessions_revoked": revoked, "family": existing.family_id})
        raise RefreshRefused(
            "This session was already replaced. For safety every session for this "
            "account has been ended - please sign in again.", "token_reuse")

    if is_expired(existing.expires_at):
        raise RefreshRefused("Session expired. Sign in again.", "session_expired")

    last = existing.last_used_at
    if last is not None:
        if last.tzinfo is None:
            last = last.replace(tzinfo=timezone.utc)
        idle = (datetime.now(timezone.utc) - last).total_seconds() / 60
        if idle > settings.session_idle_minutes:
            existing.revoked_at = datetime.now(timezone.utc)
            existing.revoked_reason = "idle_timeout"
            db.commit()
            raise RefreshRefused("Session timed out through inactivity. Sign in again.",
                                 "idle_timeout")

    now = datetime.now(timezone.utc)
    raw = new_refresh_token()
    successor = UserSession(
        user_id=existing.user_id, user_kind=existing.user_kind,
        tenant_id=existing.tenant_id, subject=existing.subject, role=existing.role,
        refresh_hash=hash_refresh_token(raw), family_id=existing.family_id,
        rotated_from=existing.id, expires_at=existing.expires_at, last_used_at=now,
        user_agent=(user_agent or existing.user_agent)[:255],
        ip_addr=(ip_addr or existing.ip_addr)[:45],
        mfa_satisfied=existing.mfa_satisfied)
    existing.revoked_at, existing.revoked_reason = now, "rotated"
    db.add(successor)
    db.commit()
    return successor, raw


# --------------------------------------------------------------- backup codes
def consume_backup_code(db: Session, identity: Identity, code: str) -> bool:
    row = db.scalar(select(MfaBackupCode).where(
        MfaBackupCode.user_kind == identity.kind,
        MfaBackupCode.user_id == identity.user_id,
        MfaBackupCode.code_hash == hash_backup_code(code),
        MfaBackupCode.used_at.is_(None)))
    if row is None:
        return False
    row.used_at = datetime.now(timezone.utc)
    db.commit()
    record_audit(
        service="tenant-service", action="auth.backup_code_used", actor=identity.subject,
        actor_role=identity.role, tenant_id=identity.tenant_id, status="success",
        detail={"remaining": remaining_backup_codes(db, identity)})
    return True


def remaining_backup_codes(db: Session, identity: Identity) -> int:
    return len(db.scalars(select(MfaBackupCode).where(
        MfaBackupCode.user_kind == identity.kind,
        MfaBackupCode.user_id == identity.user_id,
        MfaBackupCode.used_at.is_(None))).all())


def replace_backup_codes(db: Session, identity: Identity, codes: list[str]) -> None:
    """Issuing a new set invalidates the old one - a printed sheet that is still live
    after re-enrolment is a credential nobody is tracking."""
    for old in db.scalars(select(MfaBackupCode).where(
            MfaBackupCode.user_kind == identity.kind,
            MfaBackupCode.user_id == identity.user_id)).all():
        db.delete(old)
    for c in codes:
        db.add(MfaBackupCode(user_id=identity.user_id, user_kind=identity.kind,
                             code_hash=hash_backup_code(c)))
    db.commit()


def password_ok(identity: Identity, password: str) -> bool:
    return verify_password(password, identity.record.password_hash)
