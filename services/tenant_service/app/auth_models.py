"""Session and second-factor state.

Kept beside the user tables rather than inside them because these rows have a different
lifecycle: a user is long-lived, a session is disposable, and a backup code is single-use.
Mixing them would mean a logout rewriting a user record.

Both models carry ``user_kind`` alongside ``user_id``. Platform staff and tenant users live
in separate tables and a tenant user's email is unique only within its tenant, so the
identity of a session is the pair, never the email.
"""
import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Index, String, func
from sqlalchemy.orm import Mapped, mapped_column

from cp_common import Base
from cp_common.schemas_db import TENANT


def _uuid() -> str:
    return str(uuid.uuid4())


class UserSession(Base):
    """One refresh token, and the audit of how it was used.

    Access tokens are short-lived and self-contained, which makes them fast to verify and
    impossible to revoke. The session is what actually gets revoked: a logout, an
    administrator disabling an account, or detected token theft ends the session, and the
    next refresh fails even though the outstanding access token remains briefly valid.
    """

    __tablename__ = "user_sessions"
    __table_args__ = (
        Index("ix_session_user", "user_kind", "user_id", "revoked_at"),
        {"schema": TENANT},
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(String(36), index=True)
    user_kind: Mapped[str] = mapped_column(String(10))          # platform | tenant
    tenant_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    subject: Mapped[str] = mapped_column(String(255), index=True)
    role: Mapped[str] = mapped_column(String(32))

    #: Only the hash is stored. A stolen database must not yield usable refresh tokens.
    refresh_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    #: Rotation chain. Every refresh issues a new token and retires the old one, so a
    #: token presented twice is either a replay or a theft - see ``family_id``.
    family_id: Mapped[str] = mapped_column(String(36), index=True)
    rotated_from: Mapped[str | None] = mapped_column(String(36), nullable=True)

    issued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),
                                                server_default=func.now())
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    #: Drives idle timeout, which is a different control from absolute expiry.
    last_used_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),
                                                   server_default=func.now())
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True),
                                                        nullable=True)
    revoked_reason: Mapped[str] = mapped_column(String(40), default="", server_default="")

    user_agent: Mapped[str] = mapped_column(String(255), default="", server_default="")
    ip_addr: Mapped[str] = mapped_column(String(45), default="", server_default="")
    #: Whether the second factor was satisfied for this session.
    mfa_satisfied: Mapped[bool] = mapped_column(Boolean, default=False,
                                                server_default="false")


class MfaBackupCode(Base):
    """Single-use recovery codes, stored hashed.

    A user who loses their authenticator device otherwise needs an administrator to
    disable their second factor, which is both a support burden and the weakest link in
    the whole scheme. Recovery codes keep that path in the user's own hands and leave an
    audit record when one is spent.
    """

    __tablename__ = "mfa_backup_codes"
    __table_args__ = (
        Index("ix_backup_user", "user_kind", "user_id", "used_at"),
        {"schema": TENANT},
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(String(36), index=True)
    user_kind: Mapped[str] = mapped_column(String(10))
    code_hash: Mapped[str] = mapped_column(String(64), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),
                                                 server_default=func.now())
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True),
                                                     nullable=True)
