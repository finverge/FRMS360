"""Data tier — ORM models owned by the tenant-service."""
import uuid
from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, JSON, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from cp_common.crypto import EncryptedSecret
from cp_common import Base
from cp_common.schemas_db import TENANT


def _uuid() -> str:
    return str(uuid.uuid4())


def _now() -> datetime:
    return datetime.now(timezone.utc)


class PlatformUser(Base):
    """Your staff. Can administer every tenant."""
    __tablename__ = "platform_users"
    __table_args__ = {"schema": TENANT}

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(32), default="platform_admin")
    # Forces a password change before any other action is permitted.
    must_change_password: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    password_changed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # ---- second factor and lockout -------------------------------------------
    # An authenticator secret, base32. Absent until the user enrols; enabling is a
    # separate step so a half-finished enrolment cannot lock anyone out.
    mfa_secret: Mapped[str] = mapped_column(
        # Sealed at rest. A TOTP seed in plaintext lets anyone with SELECT mint a
        # valid second factor for this user, which makes MFA decorative.
        EncryptedSecret("tenant.mfa_secret"), default="", server_default="")
    mfa_enabled: Mapped[bool] = mapped_column(Boolean, default=False,
                                              server_default="false")
    mfa_enrolled_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True)
    # Consecutive failures. Reset on any successful authentication, so a user who
    # mistypes twice and then succeeds is not creeping toward a lockout for the rest
    # of the day.
    failed_attempts: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    locked_until: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True)
    last_login_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Tenant(Base):
    """A bank / regulated entity onboarded onto the platform."""
    __tablename__ = "tenants"
    __table_args__ = {"schema": TENANT}

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    slug: Mapped[str] = mapped_column(String(63), unique=True, index=True)
    legal_name: Mapped[str] = mapped_column(String(255))
    display_name: Mapped[str] = mapped_column(String(255))
    region: Mapped[str] = mapped_column(String(32), default="in-mumbai")
    plan: Mapped[str] = mapped_column(String(32), default="standard")
    # Which RBI Master Direction governs this entity. RBI's 31-July-2026 restructuring
    # (RBI/DoS/2026-27/412-463) covers each of the eleven entity types below with its
    # own instrument - see config_service/app/policy.py's ENTITY_TYPES, the single
    # source of truth for the citation text - so this drives the tenant's FRM policy
    # defaults and which Direction is named on its filings.
    entity_type: Mapped[str] = mapped_column(
        String(24), default="commercial_bank", server_default="commercial_bank")
    # RBI's four-tier framework for UCBs; supervisory expectation scales with tier.
    ucb_tier: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="provisioning")  # provisioning|active|suspended|degraded
    # Whether this bank mandates a second factor, and for whom. The obligation is the
    # entity's, not the platform's, so it is the entity that decides - but "optional"
    # is a choice its board has to make rather than a default it never saw.
    # optional | privileged | all
    mfa_policy: Mapped[str] = mapped_column(String(16), default="privileged",
                                            server_default="privileged")
    # BR-109: created through POST /tenants/self-service rather than a platform_admin's
    # POST /tenants. Fully functional for integration testing, but isolated from the
    # regulatory fraud record - ingestion refuses a sandbox tenant's batch if it claims
    # source="live" (see routes/ingest.py), so sandbox traffic can never be miscounted
    # as the traffic BR-508/BR-611 build regulatory filings from. Promotion to a real,
    # billable tenant is a deliberate platform_admin action (POST .../promote), not
    # something self-service ever grants on its own - see services.py's LIFECYCLE.
    is_sandbox: Mapped[bool] = mapped_column(Boolean, default=False,
                                             server_default="false")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=_now
    )

    users: Mapped[list["TenantUser"]] = relationship(back_populates="tenant", cascade="all, delete-orphan")


class TenantUser(Base):
    """An administrator belonging to a single tenant (bank)."""
    __tablename__ = "tenant_users"
    __table_args__ = (UniqueConstraint("tenant_id", "email", name="uq_tenant_user_email"),
        {"schema": TENANT},
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    # Schema-qualified: both tables live in the tenant service's own schema, and an
    # unqualified name no longer resolves now that `public` is empty.
    tenant_id: Mapped[str] = mapped_column(
        String(36), ForeignKey(f"{TENANT}.tenants.id", ondelete="CASCADE"), index=True)
    email: Mapped[str] = mapped_column(String(255), index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(32), default="tenant_admin")
    # Invited users start with a temporary password and must change it at first login.
    must_change_password: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    password_changed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # ---- second factor and lockout -------------------------------------------
    # An authenticator secret, base32. Absent until the user enrols; enabling is a
    # separate step so a half-finished enrolment cannot lock anyone out.
    mfa_secret: Mapped[str] = mapped_column(
        # Sealed at rest. A TOTP seed in plaintext lets anyone with SELECT mint a
        # valid second factor for this user, which makes MFA decorative.
        EncryptedSecret("tenant.mfa_secret"), default="", server_default="")
    mfa_enabled: Mapped[bool] = mapped_column(Boolean, default=False,
                                              server_default="false")
    mfa_enrolled_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True)
    # Consecutive failures. Reset on any successful authentication, so a user who
    # mistypes twice and then succeeds is not creeping toward a lockout for the rest
    # of the day.
    failed_attempts: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    locked_until: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True)
    last_login_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    tenant: Mapped[Tenant] = relationship(back_populates="users")


class TenantRole(Base):
    """BR-113 (phase 2a): a tenant's own materialised copy of a role definition.

    Seeded per tenant from the platform's fixed catalogue (cp_common.rbac) at
    onboarding - the same pattern BR-111 already uses to seed the EWS rule catalogue:
    a bank's own row, not a shared template, so retuning one tenant's role can never
    touch another's. ``source`` distinguishes a row that started life as one of the
    ten seeded roles from one a tenant later creates itself.

    Read by the role-catalogue viewer (BR-112). Writable via
    ``services/tenant_service/app/routes/tenants.py``'s role endpoints (BR-113 phase
    2b), guarded by ``roles.validate_grant`` and, for a privilege-raising edit, a
    maker-checker confirmation (``pending_change``/``proposed_by`` below). Consulted
    for authorisation only *within tenant-service itself* (``roles.capability()``) -
    the other five services have no grant on this schema and still resolve every
    existing role check against cp_common.rbac unchanged. See the module docstring on
    ``roles.py`` for the honest scope of what remains.
    """
    __tablename__ = "tenant_roles"
    __table_args__ = (UniqueConstraint("tenant_id", "name", name="uq_tenant_role_name"),
        {"schema": TENANT},
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    tenant_id: Mapped[str] = mapped_column(
        String(36), ForeignKey(f"{TENANT}.tenants.id", ondelete="CASCADE"), index=True)
    # The stable slug every existing string-typed role reference already uses -
    # TenantUser.role, AuditLog.actor_role, JWT claims. Never re-keyed to a surrogate
    # id, so nothing that already depends on the name has to change.
    name: Mapped[str] = mapped_column(String(32))
    label: Mapped[str] = mapped_column(String(120))
    modules: Mapped[list] = mapped_column(JSON, default=list)
    dashboards: Mapped[list] = mapped_column(JSON, default=list)
    can_admin_tenant: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    can_reveal_pii: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    # May propose or confirm a configuration activation (BR-715) - the
    # risk_manager-equivalent grant, narrower than can_admin_tenant. See
    # cp_common.rbac.Role.can_activate_config for why this is a separate flag rather
    # than folded into admin scope.
    can_activate_config: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    # Gated actions this role may perform (cp_common.permissions.PERMISSIONS keys). This, with
    # the three flags above and the module/dashboard lists, is the whole of what a role can do:
    # no service decides by role name.
    permissions: Mapped[list] = mapped_column(JSON, default=list, server_default="[]")
    description: Mapped[str] = mapped_column(String(600), default="", server_default="")
    # seeded = still an unedited copy of the platform catalogue; custom = created by
    # the tenant, or a seeded row the tenant has since edited.
    source: Mapped[str] = mapped_column(String(16), default="seeded", server_default="seeded")
    # A staged can_admin_tenant/can_reveal_pii/can_activate_config grant, awaiting
    # confirmation by a different eligible actor (roles.confirm_elevation) - the same
    # maker-checker asymmetry BR-715 applies elsewhere: lowering a privilege applies
    # immediately, raising one needs a second person. Null when there is nothing pending.
    pending_change: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    proposed_by: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=_now
    )
