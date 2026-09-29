"""Machine credentials for bank systems that send us transactions.

A payment switch, a CBS extract job and an ETL pipeline all need to call the intake and
decision APIs, and none of them can complete a TOTP challenge. The temptation is to turn
MFA off for a user account. That is the wrong fix twice over: it creates a human-shaped
account whose second factor is disabled — a switch someone will eventually flip on a real
person — and it leaves every ingested batch attributed to whichever employee's
credentials were pasted into the switch's config.

So this is a **different class of credential**, not a relaxed user. It has no interactive
login path, no password reset, no console session, and it can never acquire one.

**What replaces the second factor.** MFA exists because secrets leak. Removing it without
replacing what it was doing would leave the posture worse than today, so a machine
credential carries compensating controls instead:

* an optional **client-certificate thumbprint**, which is the machine equivalent of
  possession — a leaked secret alone is then not enough;
* a **scope** that cannot reach anything but intake and decisioning, so a stolen intake
  key never becomes a data-exfiltration key;
* an **IP allow-list**, because a switch has fixed egress;
* **expiry and rotation with overlap**, so rotating is not an outage;
* **per-credential attribution**, so an investigation can say which system sent a batch.

On the certificate: where the bank terminates TLS at its own edge, the thumbprint arrives
in a header from that edge rather than from the connection. That is a weaker guarantee and
it is recorded as such on the credential — ``cert_source`` says which, so nobody later
assumes a header-supplied thumbprint proved possession.
"""
from __future__ import annotations

import hashlib
import ipaddress
import secrets
from datetime import datetime, timedelta, timezone

from sqlalchemy import Boolean, DateTime, Index, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from cp_common.crypto import EncryptedSecret
from cp_common.db import Base
from cp_common.schemas_db import TENANT, table_args

#: What a machine credential may do. Deliberately short, and deliberately not "full".
#: Adding a scope here is a decision about blast radius, which is why they are named
#: rather than composed from role capabilities.
SCOPES = {
    "ingest": "Submit transactions and CBS events",
    "decide": "Request an inline decision",
    "ingest+decide": "Both of the above",
}

#: How the certificate thumbprint reached us, and therefore what it proves.
CERT_FROM_CONNECTION = "mtls"     # the TLS peer certificate; proves possession
CERT_FROM_HEADER = "header"       # forwarded by the bank's edge; trusts that edge
CERT_NONE = "none"

DEFAULT_LIFETIME_DAYS = 365
#: Client secrets are shown once. 32 bytes of urandom, so brute force is not the risk -
#: leakage is, which is what the other controls address.
SECRET_BYTES = 32


class ServiceCredentialError(Exception):
    """Refused. Every message names the control that refused and why it exists."""


class ServiceCredential(Base):
    """One machine identity belonging to one tenant."""

    __tablename__ = "service_credentials"
    __table_args__ = table_args(
        TENANT,
        UniqueConstraint("client_id", name="uq_service_credential_client_id"),
        Index("ix_service_credential_tenant", "tenant_id", "status"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(36), index=True)
    #: Human label — "UPI switch, primary DC". Shown in the audit trail, so it should say
    #: which system this is rather than who created it.
    name: Mapped[str] = mapped_column(String(120))
    client_id: Mapped[str] = mapped_column(String(48), index=True)
    #: Hashed, never recoverable. The plaintext is returned once at creation.
    secret_hash: Mapped[str] = mapped_column(String(128))
    scope: Mapped[str] = mapped_column(String(24), default="ingest")

    #: Empty means no restriction, which is recorded as a weaker posture rather than
    #: silently treated as fine - see ``describe_posture``.
    ip_allowlist: Mapped[list] = mapped_column(JSONB, default=list)
    cert_thumbprint: Mapped[str] = mapped_column(
        EncryptedSecret("service_credential.cert_thumbprint"), default="",
        server_default="")
    cert_source: Mapped[str] = mapped_column(String(12), default=CERT_NONE)

    status: Mapped[str] = mapped_column(String(12), default="active", index=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True),
                                                        nullable=True)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True),
                                                          nullable=True)
    #: Set when this credential replaces another during a rotation, so the overlap is
    #: visible and the old one can be retired deliberately rather than forgotten.
    rotated_from: Mapped[str] = mapped_column(String(36), default="", server_default="")
    created_by: Mapped[str] = mapped_column(String(120), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),
                                                 server_default=func.now())
    revoked_reason: Mapped[str] = mapped_column(Text, default="", server_default="")


# ------------------------------------------------------------------ minting
def new_client_id() -> str:
    return "svc_" + secrets.token_hex(12)


def new_secret() -> str:
    return secrets.token_urlsafe(SECRET_BYTES)


def hash_secret(secret: str) -> str:
    """SHA-256, not a password KDF.

    A 32-byte random secret has no guessable structure, so key stretching buys nothing
    and would put a deliberate delay on the token endpoint - which a switch calls on a
    schedule. Human passwords still use the slow hash; these are not passwords.
    """
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()


def verify_secret(secret: str, stored_hash: str) -> bool:
    return secrets.compare_digest(hash_secret(secret or ""), stored_hash or "")


def normalise_thumbprint(value: str) -> str:
    """Certificate thumbprints arrive colon-separated, spaced, upper or lower case."""
    return "".join(c for c in (value or "").lower() if c in "0123456789abcdef")


# ------------------------------------------------------------------ validation
def validate_scope(scope: str) -> str:
    s = (scope or "").strip().lower()
    if s not in SCOPES:
        raise ServiceCredentialError(
            f"'{scope}' is not a machine scope. One of: {', '.join(sorted(SCOPES))}. "
            "A machine credential cannot be given full access - that is what makes a "
            "leaked intake key survivable.")
    return s


def validate_allowlist(entries) -> list[str]:
    """Each entry must parse as an address or network, or the restriction is a fiction."""
    out: list[str] = []
    for raw in entries or []:
        text = str(raw).strip()
        if not text:
            continue
        try:
            ipaddress.ip_network(text, strict=False)
        except ValueError as exc:
            raise ServiceCredentialError(
                f"'{text}' is not an IP address or CIDR block. An allow-list entry that "
                "does not parse would silently never match, leaving the credential "
                "usable from anywhere.") from exc
        out.append(text)
    return out


def ip_allowed(remote: str, allowlist) -> bool:
    if not allowlist:
        return True
    try:
        addr = ipaddress.ip_address((remote or "").strip())
    except ValueError:
        # An unparseable caller address cannot be shown to be on the list, so it is not.
        return False
    return any(addr in ipaddress.ip_network(entry, strict=False) for entry in allowlist)


def check_usable(cred: ServiceCredential, *, now: datetime | None = None) -> None:
    """Raise unless this credential may be used at all, right now."""
    now = now or datetime.now(timezone.utc)
    if cred.status != "active":
        raise ServiceCredentialError(
            f"This credential is {cred.status}"
            + (f": {cred.revoked_reason}" if cred.revoked_reason else "."))
    if cred.expires_at is not None:
        expires = cred.expires_at
        if expires.tzinfo is None:
            expires = expires.replace(tzinfo=timezone.utc)
        if expires < now:
            raise ServiceCredentialError(
                f"This credential expired on {expires.date()}. Rotate it - credentials "
                "expire so that a forgotten integration stops working rather than "
                "keeping a key alive forever.")


def check_certificate(cred: ServiceCredential, presented: str) -> None:
    """Enforce cert binding when the credential declares one."""
    expected = normalise_thumbprint(cred.cert_thumbprint or "")
    if not expected:
        return
    if not secrets.compare_digest(expected, normalise_thumbprint(presented)):
        raise ServiceCredentialError(
            "The client certificate does not match the one bound to this credential. "
            "The secret alone is not sufficient - that is the point of binding it.")


def describe_posture(cred: ServiceCredential) -> dict:
    """What this credential actually proves, for the console and an IS review.

    Stated rather than scored: "possession proven by mTLS" and "secret only, usable from
    anywhere" are both legitimate configurations for different deployments, and the
    reviewer needs to see which one is in force.
    """
    bound = bool(normalise_thumbprint(cred.cert_thumbprint or ""))
    return {
        "secret": "hashed, shown once at creation",
        "certificate": (
            "bound, verified from the TLS connection" if bound and cred.cert_source == CERT_FROM_CONNECTION
            else "bound, but supplied by a trusting edge rather than the connection"
            if bound else "not bound - the secret alone authenticates"),
        "network": (f"restricted to {len(cred.ip_allowlist)} range(s)"
                    if cred.ip_allowlist else "unrestricted - usable from any address"),
        "scope": SCOPES.get(cred.scope, cred.scope),
        "expiry": cred.expires_at.date().isoformat() if cred.expires_at else "none set",
        "proves_possession": bound and cred.cert_source == CERT_FROM_CONNECTION,
    }


def default_expiry(days: int = DEFAULT_LIFETIME_DAYS) -> datetime:
    return datetime.now(timezone.utc) + timedelta(days=days)
