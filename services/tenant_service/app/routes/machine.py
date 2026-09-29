"""Machine credential issuance and management.

Two audiences, deliberately separated:

* ``POST /machine/token`` is called by the bank's system on a schedule. It is the only
  unauthenticated endpoint here, because presenting the credential *is* the
  authentication, and it returns a short-lived scoped token.
* the rest is tenant administration, and requires a human with tenant-admin rights.

Every issuance and every refusal is audited. A refusal is the more interesting record:
a credential being tried from an unlisted address, or after expiry, is what an
investigation wants to find.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Header, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from cp_common import AppError, Principal, get_current_principal, get_session, record_audit
from cp_common import tenant_status
from cp_common.security import create_access_token
from cp_common.tenancy import resolve_tenant_scope

from .. import service_credentials as sc
from ..repositories import TenantRepository

router = APIRouter(prefix="/machine", tags=["machine"])


class TokenIn(BaseModel):
    """OAuth2 client-credentials, spelled the way every bank integration team expects."""
    grant_type: str = Field(default="client_credentials")
    client_id: str = Field(max_length=48)
    client_secret: str = Field(max_length=128)


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "Bearer"
    expires_in: int
    scope: str
    tenant_id: str


class CredentialIn(BaseModel):
    name: str = Field(min_length=3, max_length=120)
    scope: str = Field(default="ingest")
    ip_allowlist: list[str] = Field(default_factory=list)
    cert_thumbprint: str = Field(default="", max_length=256)
    cert_source: str = Field(default=sc.CERT_NONE)
    expires_in_days: int = Field(default=sc.DEFAULT_LIFETIME_DAYS, ge=1, le=1095)


class CredentialOut(BaseModel):
    id: str
    name: str
    client_id: str
    scope: str
    status: str
    ip_allowlist: list[str]
    cert_source: str
    expires_at: datetime | None
    last_used_at: datetime | None
    created_at: datetime
    posture: dict
    #: Present exactly once, on creation and rotation. Never stored in plaintext and
    #: never retrievable afterwards - if it is lost, rotate.
    client_secret: str | None = None


def _out(cred: sc.ServiceCredential, secret: str | None = None) -> CredentialOut:
    return CredentialOut(
        id=cred.id, name=cred.name, client_id=cred.client_id, scope=cred.scope,
        status=cred.status, ip_allowlist=list(cred.ip_allowlist or []),
        cert_source=cred.cert_source, expires_at=cred.expires_at,
        last_used_at=cred.last_used_at, created_at=cred.created_at,
        posture=sc.describe_posture(cred), client_secret=secret)


def _client_ip(request: Request) -> str:
    """The caller's address as the edge saw it.

    X-Forwarded-For is trusted here because the only path to this service is through our
    own load balancer, which overwrites it. If that ever stops being true, this is the
    line that has to change.
    """
    fwd = request.headers.get("x-forwarded-for", "")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else ""


# ------------------------------------------------------------------ the hot path
@router.post("/token", response_model=TokenOut)
def issue_token(
    body: TokenIn,
    request: Request,
    db: Session = Depends(get_session),
    x_client_cert_thumbprint: str = Header(default=""),
) -> TokenOut:
    """Exchange a machine credential for a short-lived scoped token.

    No MFA, and none is being bypassed: this credential class never had an interactive
    login path. What stands in for possession is the certificate binding, the address
    restriction and the scope — see the module docstring on service_credentials.
    """
    if body.grant_type != "client_credentials":
        raise AppError("Only the client_credentials grant is supported here", 400,
                       "unsupported_grant")

    cred = db.execute(
        select(sc.ServiceCredential).where(
            sc.ServiceCredential.client_id == body.client_id)).scalar_one_or_none()

    # One message for "no such client" and "wrong secret", so the endpoint cannot be used
    # to enumerate which client ids exist.
    def _refuse(reason: str, code: str = "invalid_client") -> AppError:
        record_audit(
            service="tenant-service", action="machine.token", actor=body.client_id,
            actor_role="service", tenant_id=cred.tenant_id if cred else None,
            target_type="service_credential", target_id=cred.id if cred else "",
            status="refused", detail={"reason": reason, "ip": _client_ip(request)})
        return AppError("Invalid client credentials", 401, code)

    if cred is None or not sc.verify_secret(body.client_secret, cred.secret_hash):
        raise _refuse("unknown client id or wrong secret")

    try:
        sc.check_usable(cred)
    except sc.ServiceCredentialError as exc:
        # This one *is* specific: the caller holds a valid secret, so telling them the
        # credential has expired is useful rather than a disclosure.
        record_audit(
            service="tenant-service", action="machine.token", actor=cred.client_id,
            actor_role="service", tenant_id=cred.tenant_id,
            target_type="service_credential", target_id=cred.id, status="refused",
            detail={"reason": str(exc), "ip": _client_ip(request)})
        raise AppError(str(exc), 403, "credential_unusable")

    if not sc.ip_allowed(_client_ip(request), cred.ip_allowlist or []):
        raise _refuse(f"address {_client_ip(request)} is not on the allow-list",
                      "ip_not_allowed")

    try:
        sc.check_certificate(cred, x_client_cert_thumbprint)
    except sc.ServiceCredentialError as exc:
        raise _refuse(str(exc), "certificate_mismatch")

    # The credential itself is fine; the tenant may not be. Checked here against the
    # register directly - this service owns it, so unlike intake there is nothing to
    # cache and a suspension bites on the very next token request.
    tenant = TenantRepository(db).get(cred.tenant_id)
    if tenant and tenant.status in tenant_status.NOT_SERVED:
        record_audit(
            service="tenant-service", action="machine.token", actor=cred.client_id,
            actor_role="service", tenant_id=cred.tenant_id,
            target_type="service_credential", target_id=cred.id, status="refused",
            detail={"reason": f"tenant is {tenant.status}", "ip": _client_ip(request)})
        raise AppError(tenant_status.WHY_NOT_SERVED[tenant.status], 403,
                       f"tenant_{tenant.status}")

    cred.last_used_at = datetime.now(timezone.utc)
    db.commit()

    from cp_common import settings
    token = create_access_token(subject=cred.client_id, role="service",
                                tenant_id=cred.tenant_id, scope=cred.scope)
    record_audit(
        service="tenant-service", action="machine.token", actor=cred.client_id,
        actor_role="service", tenant_id=cred.tenant_id,
        target_type="service_credential", target_id=cred.id, status="success",
        detail={"scope": cred.scope, "ip": _client_ip(request)})
    return TokenOut(access_token=token, expires_in=settings.jwt_expire_minutes * 60,
                    scope=cred.scope, tenant_id=cred.tenant_id)


# ------------------------------------------------------------------ administration
@router.get("/{tenant_id}/credentials", response_model=list[CredentialOut])
def list_credentials(
    tenant_id: str,
    db: Session = Depends(get_session),
    principal: Principal = Depends(get_current_principal),
) -> list[CredentialOut]:
    resolve_tenant_scope(principal, tenant_id)
    rows = db.execute(
        select(sc.ServiceCredential)
        .where(sc.ServiceCredential.tenant_id == tenant_id)
        .order_by(sc.ServiceCredential.created_at.desc())).scalars().all()
    return [_out(r) for r in rows]


@router.post("/{tenant_id}/credentials", response_model=CredentialOut, status_code=201)
def create_credential(
    tenant_id: str,
    payload: CredentialIn,
    db: Session = Depends(get_session),
    principal: Principal = Depends(get_current_principal),
) -> CredentialOut:
    resolve_tenant_scope(principal, tenant_id)
    if not principal.is_platform_admin and principal.role != "tenant_admin":
        raise AppError("Only a tenant administrator may issue machine credentials",
                       403, "role_not_permitted")
    try:
        scope = sc.validate_scope(payload.scope)
        allowlist = sc.validate_allowlist(payload.ip_allowlist)
    except sc.ServiceCredentialError as exc:
        raise AppError(str(exc), 400, "invalid_credential")

    if payload.cert_thumbprint and payload.cert_source not in (
            sc.CERT_FROM_CONNECTION, sc.CERT_FROM_HEADER):
        raise AppError(
            "cert_source must say how the thumbprint reaches us — 'mtls' when it comes "
            "from the TLS connection, 'header' when the bank's edge forwards it. The two "
            "prove different things and must not be recorded as the same.",
            400, "cert_source_required")

    secret = sc.new_secret()
    cred = sc.ServiceCredential(
        id=str(uuid.uuid4()), tenant_id=tenant_id, name=payload.name.strip(),
        client_id=sc.new_client_id(), secret_hash=sc.hash_secret(secret), scope=scope,
        ip_allowlist=allowlist,
        cert_thumbprint=sc.normalise_thumbprint(payload.cert_thumbprint),
        cert_source=(payload.cert_source if payload.cert_thumbprint else sc.CERT_NONE),
        status="active", expires_at=sc.default_expiry(payload.expires_in_days),
        created_by=principal.subject)
    db.add(cred)
    db.commit()
    db.refresh(cred)
    record_audit(
        service="tenant-service", action="machine.credential.create",
        actor=principal.subject, actor_role=principal.role, tenant_id=tenant_id,
        target_type="service_credential", target_id=cred.id, status="success",
        detail={"name": cred.name, "scope": scope, "posture": sc.describe_posture(cred)})
    return _out(cred, secret=secret)


@router.post("/{tenant_id}/credentials/{cred_id}/rotate", response_model=CredentialOut)
def rotate_credential(
    tenant_id: str,
    cred_id: str,
    db: Session = Depends(get_session),
    principal: Principal = Depends(get_current_principal),
) -> CredentialOut:
    """Issue a replacement, leaving the old one live.

    Overlap is the point: revoking on rotation means the switch stops working the moment
    a key is rotated, which is how rotation ends up never being done.
    """
    resolve_tenant_scope(principal, tenant_id)
    old = db.get(sc.ServiceCredential, cred_id)
    if old is None or old.tenant_id != tenant_id:
        raise AppError("Credential not found", 404, "not_found")

    secret = sc.new_secret()
    new = sc.ServiceCredential(
        id=str(uuid.uuid4()), tenant_id=tenant_id, name=f"{old.name} (rotated)",
        client_id=sc.new_client_id(), secret_hash=sc.hash_secret(secret),
        scope=old.scope, ip_allowlist=list(old.ip_allowlist or []),
        cert_thumbprint=old.cert_thumbprint, cert_source=old.cert_source,
        status="active", expires_at=sc.default_expiry(), rotated_from=old.id,
        created_by=principal.subject)
    db.add(new)
    db.commit()
    db.refresh(new)
    record_audit(
        service="tenant-service", action="machine.credential.rotate",
        actor=principal.subject, actor_role=principal.role, tenant_id=tenant_id,
        target_type="service_credential", target_id=new.id, status="success",
        detail={"rotated_from": old.id,
                "note": "the previous credential remains active until revoked"})
    return _out(new, secret=secret)


@router.post("/{tenant_id}/credentials/{cred_id}/revoke", response_model=CredentialOut)
def revoke_credential(
    tenant_id: str,
    cred_id: str,
    reason: str = "",
    db: Session = Depends(get_session),
    principal: Principal = Depends(get_current_principal),
) -> CredentialOut:
    resolve_tenant_scope(principal, tenant_id)
    cred = db.get(sc.ServiceCredential, cred_id)
    if cred is None or cred.tenant_id != tenant_id:
        raise AppError("Credential not found", 404, "not_found")
    cred.status = "revoked"
    cred.revoked_reason = (reason or "revoked by administrator")[:500]
    db.commit()
    db.refresh(cred)
    record_audit(
        service="tenant-service", action="machine.credential.revoke",
        actor=principal.subject, actor_role=principal.role, tenant_id=tenant_id,
        target_type="service_credential", target_id=cred.id, status="success",
        detail={"reason": cred.revoked_reason})
    return _out(cred)
