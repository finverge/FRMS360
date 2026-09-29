"""Per-tenant federated identity configuration.

Federation is a tenant-level concern, not a platform one. Each bank runs its own identity
provider, mandates its own factors there, and expects joiners and leavers to be handled by
its own directory rather than by a vendor's user list. So the configuration hangs off the
tenant, and the platform holds no opinion beyond the protocol.

One deliberate constraint: **role mapping is explicit**. A claim from an IdP is an
assertion by that bank's directory about one of its own staff, and it is trusted for
identity. It is not trusted to name a role on this platform unless the tenant has said
which of its groups maps to which role. Anything unmapped gets the configured default,
which starts at the least-privileged role rather than at whatever the token happened to
say - a directory group called "admin" must not become a platform administrator by
coincidence of naming.
"""
import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Index, JSON, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from cp_common import Base
from cp_common.crypto import EncryptedSecret
from cp_common.schemas_db import TENANT


def _uuid() -> str:
    return str(uuid.uuid4())


class IdentityProvider(Base):
    """One federated login source for one tenant.

    Modelled as a list rather than a single row because a bank mid-migration genuinely
    runs two - the old ADFS and the new Entra tenant - and needs both to work while it
    moves.
    """

    __tablename__ = "identity_providers"
    __table_args__ = (
        UniqueConstraint("tenant_id", "slug", name="uq_idp_slug"),
        {"schema": TENANT},
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    tenant_id: Mapped[str] = mapped_column(String(36), index=True)
    #: Appears in the sign-in URL, so it is stable and human-readable.
    slug: Mapped[str] = mapped_column(String(63))
    display_name: Mapped[str] = mapped_column(String(120))
    #: oidc today. saml is the other one banks ask for; the column exists so adding it
    #: does not require a migration on a live tenant.
    protocol: Mapped[str] = mapped_column(String(10), default="oidc",
                                          server_default="oidc")

    issuer: Mapped[str] = mapped_column(String(255))
    client_id: Mapped[str] = mapped_column(String(255))
    #: Confidential clients only. A public client would mean anyone could impersonate the
    #: platform to the bank's IdP.
    client_secret: Mapped[str] = mapped_column(
        EncryptedSecret("tenant.idp_client_secret"), default="", server_default="")
    #: Discovery is preferred; the explicit endpoints exist for providers that do not
    #: publish a well-known document, which is still common behind bank firewalls.
    discovery_url: Mapped[str] = mapped_column(String(255), default="",
                                               server_default="")
    authorize_url: Mapped[str] = mapped_column(String(255), default="",
                                               server_default="")
    token_url: Mapped[str] = mapped_column(String(255), default="", server_default="")
    jwks_url: Mapped[str] = mapped_column(String(255), default="", server_default="")
    scopes: Mapped[str] = mapped_column(String(255), default="openid email profile",
                                        server_default="openid email profile")

    # ---- SAML 2.0 -----------------------------------------------------------------
    #: Where the browser is sent to authenticate (IdP SSO endpoint, redirect binding).
    saml_sso_url: Mapped[str] = mapped_column(String(500), default="", server_default="")
    #: The IdP's signing certificate, PEM. Without it every signature check is vacuous,
    #: so the verifier refuses rather than degrading to "unsigned is fine".
    saml_certificate: Mapped[str] = mapped_column(Text, default="", server_default="")
    #: Attribute names as this IdP emits them. ADFS sends Claims-schema URIs; Entra sends
    #: short names. Neither is standard, so both are configured rather than guessed.
    saml_email_attribute: Mapped[str] = mapped_column(
        String(255), default="", server_default="")
    saml_groups_attribute: Mapped[str] = mapped_column(
        String(255), default="", server_default="")

    #: Which claim carries the user's identity, and which carries their groups.
    email_claim: Mapped[str] = mapped_column(String(64), default="email",
                                             server_default="email")
    groups_claim: Mapped[str] = mapped_column(String(64), default="groups",
                                              server_default="groups")
    #: {"FRAUD-ANALYSTS": "analyst", ...}. Explicit, per tenant, never inferred.
    role_mapping: Mapped[dict] = mapped_column(JSON, default=dict)
    #: Applied when no group matches. Least privilege by default.
    default_role: Mapped[str] = mapped_column(String(32), default="analyst",
                                              server_default="analyst")
    #: Whether a user unknown to the platform may be created on first sign-in. Banks
    #: that want their directory to be the only source of truth turn this on; banks that
    #: want an explicit invite step leave it off.
    jit_provisioning: Mapped[bool] = mapped_column(Boolean, default=True,
                                                   server_default="true")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),
                                                 server_default=func.now())


class SamlAssertionSeen(Base):
    """Assertion ids already consumed, so one cannot be presented twice.

    A SAML assertion is a bearer credential: anyone who observes one - a proxy log, a
    browser extension, a shoulder-surfed POST body - can replay it until it expires.
    Without this table the only thing standing between an observer and a session is the
    assertion's own lifetime, which the IdP chooses and is often measured in minutes
    rather than seconds.

    Rows are pruned once the assertion could no longer be valid anyway. The verifier
    refuses assertions living longer than it will remember them, so the window is closed
    at both ends rather than merely narrowed.
    """

    __tablename__ = "saml_assertions_seen"
    __table_args__ = (
        UniqueConstraint("tenant_id", "assertion_id", name="uq_saml_assertion"),
        Index("ix_saml_assertion_expiry", "expires_at"),
        {"schema": TENANT},
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    tenant_id: Mapped[str] = mapped_column(String(36), index=True)
    provider_id: Mapped[str] = mapped_column(String(36), index=True)
    assertion_id: Mapped[str] = mapped_column(String(255))
    consumed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),
                                                  server_default=func.now())
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class SsoLoginState(Base):
    """One in-flight authorisation request.

    Holds the ``state`` and ``nonce`` that bind the callback to the browser that started
    it, plus the PKCE verifier. Stored server-side rather than in a cookie so the check
    cannot be forged by whoever controls the browser, and short-lived because an
    authorisation request that has not completed in ten minutes has been abandoned.
    """

    __tablename__ = "sso_login_states"
    __table_args__ = (
        Index("ix_sso_state_expiry", "expires_at"),
        {"schema": TENANT},
    )

    state: Mapped[str] = mapped_column(String(64), primary_key=True)
    provider_id: Mapped[str] = mapped_column(String(36), index=True)
    tenant_id: Mapped[str] = mapped_column(String(36), index=True)
    nonce: Mapped[str] = mapped_column(String(64))
    code_verifier: Mapped[str] = mapped_column(String(128))
    redirect_uri: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),
                                                 server_default=func.now())
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    #: Set once consumed, so a replayed callback cannot mint a second session.
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True),
                                                     nullable=True)
