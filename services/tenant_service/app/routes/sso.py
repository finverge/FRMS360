"""Federated sign-in, per tenant.

Two endpoints do the work: one starts an authorisation request, one receives the callback.
Everything protocol-shaped lives in ``cp_common.oidc``; what is here is the part specific
to this platform - which tenant, which provider, and what a claim from a bank's directory
is allowed to mean.

The rule worth stating plainly: **an IdP is trusted for identity, not for authority.** It
tells us who the person is. Which role they get is decided by the tenant's own mapping,
falls back to the least-privileged role when nothing matches, and can never produce
platform staff. A directory group happening to be called "admin" must not become a
platform administrator.
"""
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Form, Query, Request
from fastapi.responses import Response
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from cp_common import (
    AppError, Principal, get_current_principal, get_session, hash_password,
    record_audit, resolve_tenant_scope, settings,
)
from cp_common.oidc import (
    OidcError, ProviderEndpoints, authorize_url, discover, exchange_code, map_role,
    new_pkce, validate_id_token,
)
from cp_common import saml
from cp_common.rbac import ASSIGNABLE_TENANT_ROLES, can_admin_tenant

from sqlalchemy.exc import IntegrityError

from ..idp_models import IdentityProvider, SamlAssertionSeen, SsoLoginState
from ..models import Tenant, TenantUser
from ..schemas import IdpCreate, IdpUpdate

router = APIRouter(prefix="/auth/sso", tags=["sso"])

#: An authorisation request that has not come back in this long has been abandoned.
STATE_TTL_MINUTES = 10

#: Path segments that appear literally in a sign-in URL. A tenant or provider slug equal
#: to one of these could shadow a real endpoint - a tenant called "saml" whose provider is
#: called "acs" is the kind of thing nobody plans for and everybody eventually types.
#:
#: The current routes happen not to collide, because the literals sit at different depths.
#: That is luck, not design, and it would stop being true the first time someone adds a
#: route. ``test_sso_routing`` walks the live route table and fails if a literal segment
#: is missing from this set, so the guard cannot silently fall behind the URLs.
RESERVED_SLUGS = frozenset({
    "saml", "oidc", "acs", "metadata", "callback", "providers", "admin", "start",
    "login", "logout", "api", "auth", "sso", "internal", "health", "metrics",
})


def validate_slug(slug: str, *, what: str = "slug") -> str:
    """Refuse a slug that could shadow an endpoint, before it is ever stored."""
    s = (slug or "").strip().lower()
    if not s:
        raise AppError(f"A {what} is required", 400, "slug_required")
    if s in RESERVED_SLUGS:
        raise AppError(
            f"'{s}' is reserved because it appears in sign-in URLs. Choose another "
            f"{what}.", 400, "slug_reserved")
    if not all(c.isalnum() or c == "-" for c in s):
        raise AppError(f"A {what} may contain only letters, digits and hyphens", 400,
                       "slug_invalid")
    return s


def _endpoints(idp: IdentityProvider) -> ProviderEndpoints:
    if idp.discovery_url:
        return discover(idp.discovery_url)
    missing = [n for n, v in (("authorize_url", idp.authorize_url),
                              ("token_url", idp.token_url),
                              ("jwks_url", idp.jwks_url)) if not v]
    if missing:
        raise OidcError("This identity provider is not fully configured: missing " +
                        ", ".join(missing), "idp_misconfigured")
    return ProviderEndpoints(idp.authorize_url, idp.token_url, idp.jwks_url, idp.issuer)


def _find_provider(db: Session, tenant_slug: str, idp_slug: str) -> tuple[Tenant, IdentityProvider]:
    tenant = db.scalar(select(Tenant).where(Tenant.slug == tenant_slug))
    if tenant is None:
        raise AppError("Unknown organisation", 404, "unknown_tenant")
    if tenant.status in ("suspended", "offboarded"):
        raise AppError(f"Access disabled: tenant is {tenant.status}", 403,
                       "tenant_inactive")
    idp = db.scalar(select(IdentityProvider).where(
        IdentityProvider.tenant_id == tenant.id,
        IdentityProvider.slug == idp_slug,
        IdentityProvider.enabled.is_(True)))
    if idp is None:
        raise AppError("No such sign-in method for this organisation", 404,
                       "unknown_provider")
    return tenant, idp


@router.get("/providers/{tenant_slug}")
def providers(tenant_slug: str, db: Session = Depends(get_session)) -> dict:
    """What sign-in methods this organisation offers.

    Public by necessity - the console needs it before anyone has authenticated - so it
    returns names and nothing else. No issuer, no client id, no endpoint.
    """
    tenant = db.scalar(select(Tenant).where(Tenant.slug == tenant_slug))
    if tenant is None:
        return {"tenant": tenant_slug, "providers": []}
    rows = db.scalars(select(IdentityProvider).where(
        IdentityProvider.tenant_id == tenant.id,
        IdentityProvider.enabled.is_(True))).all()
    return {"tenant": tenant.slug, "display_name": tenant.display_name,
            "providers": [{"slug": r.slug, "display_name": r.display_name,
                           "protocol": r.protocol} for r in rows]}


@router.get("/{tenant_slug}/{idp_slug}/start")
def start(tenant_slug: str, idp_slug: str, request: Request,
          db: Session = Depends(get_session)) -> RedirectResponse:
    """Begin an authorisation request and send the browser to the provider."""
    tenant, idp = _find_provider(db, tenant_slug, idp_slug)
    try:
        endpoints = _endpoints(idp)
    except OidcError as exc:
        raise AppError(exc.message, 502, exc.code)

    state, nonce = secrets.token_urlsafe(24), secrets.token_urlsafe(24)
    verifier, challenge = new_pkce()
    # Must be the public console origin. The IdP sends the browser here, and a browser
    # cannot reach an internal service address - nor should it.
    redirect_uri = callback_url()

    db.add(SsoLoginState(
        state=state, provider_id=idp.id, tenant_id=tenant.id, nonce=nonce,
        code_verifier=verifier, redirect_uri=redirect_uri,
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=STATE_TTL_MINUTES)))
    db.commit()

    return RedirectResponse(authorize_url(
        endpoints, client_id=idp.client_id, redirect_uri=redirect_uri,
        scopes=idp.scopes, state=state, nonce=nonce, challenge=challenge), 302)


def callback_url() -> str:
    """Where the IdP returns the browser: the public console, via the gateway."""
    return settings.console_base_url.rstrip("/") + "/api/auth/sso/callback"


def _console_redirect(**params) -> RedirectResponse:
    """Hand control back to the console.

    Absolute, against the configured console origin - a relative redirect resolves
    against whichever host served the callback, which behind a gateway is the internal
    service rather than the console.

    The console completes the sign-in from a one-time handoff rather than receiving a
    session token in the URL: URLs end up in browser history, referrer headers and
    access logs, and a session token in any of those is a session token leaked.
    """
    return RedirectResponse(
        settings.console_base_url.rstrip("/") + "/?" + urlencode(params),
        status_code=303)


@router.get("/callback", name="sso_callback")
def callback(request: Request, code: str = Query(default=""),
             state: str = Query(default=""), error: str = Query(default=""),
             db: Session = Depends(get_session)) -> RedirectResponse:
    if error:
        return _console_redirect(sso_error="Your organisation's sign-in was cancelled.")

    row = db.get(SsoLoginState, state)
    if row is None or row.used_at is not None:
        # Unknown or replayed: either way this callback did not come from a request we
        # started, which is the exact shape of a login-CSRF attempt.
        return _console_redirect(sso_error="This sign-in link is no longer valid. "
                                           "Please start again.")
    if row.expires_at and row.expires_at.replace(
            tzinfo=row.expires_at.tzinfo or timezone.utc) < datetime.now(timezone.utc):
        return _console_redirect(sso_error="This sign-in took too long. Please try again.")

    row.used_at = datetime.now(timezone.utc)
    db.commit()

    idp = db.get(IdentityProvider, row.provider_id)
    tenant = db.get(Tenant, row.tenant_id)
    if idp is None or tenant is None or not idp.enabled:
        return _console_redirect(sso_error="This sign-in method is no longer available.")

    try:
        endpoints = _endpoints(idp)
        tokens = exchange_code(endpoints, code=code, client_id=idp.client_id,
                               client_secret=idp.client_secret,
                               redirect_uri=row.redirect_uri,
                               verifier=row.code_verifier)
        claims = validate_id_token(tokens["id_token"], endpoints=endpoints,
                                   client_id=idp.client_id, nonce=row.nonce,
                                   email_claim=idp.email_claim,
                                   groups_claim=idp.groups_claim)
    except OidcError as exc:
        record_audit(
            service="tenant-service", action="auth.sso_failed", actor="(sso)",
            tenant_id=tenant.id, status="failure",
            detail={"provider": idp.slug, "code": exc.code})
        return _console_redirect(sso_error=exc.message)

    role, how = map_role(claims.groups, idp.role_mapping, idp.default_role)
    # A mapping that names a role this platform does not hand to tenants - or names
    # platform staff - is a configuration error, not an instruction.
    if role not in ASSIGNABLE_TENANT_ROLES:
        record_audit(
            service="tenant-service", action="auth.sso_failed", actor=claims.email,
            tenant_id=tenant.id, status="failure",
            detail={"provider": idp.slug, "reason": "unassignable_role", "role": role})
        return _console_redirect(
            sso_error="Your organisation's sign-in mapped you to a role this platform "
                      "cannot grant. Contact your administrator.")

    return _complete_sso(db, request, tenant, idp, email=claims.email, role=role,
                         how=how, protocol="oidc")


def _complete_sso(db: Session, request: Request, tenant, idp, *, email: str, role: str,
                  how: str, protocol: str) -> RedirectResponse:
    """Provision or reconcile the user, then open a session.

    Shared by both protocols on purpose. The rules about who may be created, whose role
    the directory is allowed to change, and whether MFA is re-challenged are policy - and
    policy that differed between OIDC and SAML would be a policy nobody could state.
    """
    user = db.scalar(select(TenantUser).where(TenantUser.tenant_id == tenant.id,
                                              TenantUser.email == email))
    if user is None:
        if not idp.jit_provisioning:
            record_audit(
                service="tenant-service", action="auth.sso_denied", actor=email,
                tenant_id=tenant.id, status="failure",
                detail={"provider": idp.slug, "reason": "not_provisioned", "protocol": protocol})
            return _console_redirect(
                sso_error="You do not have an account on this platform. Ask your "
                          "administrator to invite you.")
        user = TenantUser(
            tenant_id=tenant.id, email=email, role=role,
            # Federated users never authenticate with a local password. A random,
            # discarded value keeps the column non-null without creating a usable
            # second credential that nobody is managing.
            password_hash=hash_password(secrets.token_urlsafe(32)),
            must_change_password=False)
        db.add(user)
        db.commit()
        record_audit(
            service="tenant-service", action="auth.sso_provisioned", actor=email,
            actor_role=role, tenant_id=tenant.id, status="success",
            detail={"provider": idp.slug, "role_from": how, "protocol": protocol})
    elif user.role != role:
        # The directory is authoritative for group membership, so a role change there
        # takes effect here. Recorded, because it is a privilege change.
        before = user.role
        user.role = role
        db.commit()
        record_audit(
            service="tenant-service", action="auth.sso_role_changed",
            actor=email, actor_role=role, tenant_id=tenant.id, status="success",
            detail={"provider": idp.slug, "from": before, "to": role, "basis": how})

    from .. import auth_service as svc

    identity = svc.find_identity(db, email)
    svc.register_success(db, identity)
    # The second factor was enforced by the bank's own IdP; re-challenging here would be
    # asking the same person for the same proof twice.
    _, refresh = svc.open_session(
        db, identity, mfa_satisfied=True,
        user_agent=request.headers.get("user-agent", ""),
        ip_addr=request.client.host if request.client else "")

    record_audit(
        service="tenant-service", action="auth.sso_login", actor=email,
        actor_role=role, tenant_id=tenant.id, status="success",
        detail={"provider": idp.slug, "role_from": how, "protocol": protocol})

    # The refresh token is opaque and single-purpose; the console exchanges it for an
    # access token immediately and it is revoked the moment it is used.
    return _console_redirect(sso_handoff=refresh)


# ------------------------------------------------------------ tenant administration
# Federation is a tenant-level concern (see the module docstring), so a bank's own
# tenant_admin manages it, not only platform staff - resolve_tenant_scope keeps a
# tenant_admin pinned to their own tenant, can_admin_tenant keeps everyone else out.
def _require_tenant_admin(principal: Principal, tenant_id: str) -> None:
    resolve_tenant_scope(principal, tenant_id)
    if not can_admin_tenant(principal.role):
        raise AppError("Your role may not manage identity providers", 403,
                       "role_not_permitted")


def _validate_role_fields(default_role: str, role_mapping: dict) -> None:
    """A directory group must never resolve to a role the tenant could not itself
    assign - see idp_models's "can never produce platform staff" guarantee."""
    bad = {r for r in [default_role, *role_mapping.values()]
           if r not in ASSIGNABLE_TENANT_ROLES}
    if bad:
        raise AppError(
            f"Not a role a tenant may assign: {', '.join(sorted(bad))}", 400,
            "role_not_assignable")


def _provider_out(r: IdentityProvider) -> dict:
    # The client secret and SAML certificate are never returned, not even to the
    # tenant's own administrator - only whether one is on file, so the console can
    # show "unchanged" instead of a blank that looks like nothing was ever set.
    return {"id": r.id, "slug": r.slug, "display_name": r.display_name,
            "protocol": r.protocol, "issuer": r.issuer, "client_id": r.client_id,
            "discovery_url": r.discovery_url, "authorize_url": r.authorize_url,
            "token_url": r.token_url, "jwks_url": r.jwks_url, "scopes": r.scopes,
            "saml_sso_url": r.saml_sso_url,
            "saml_email_attribute": r.saml_email_attribute,
            "saml_groups_attribute": r.saml_groups_attribute,
            "email_claim": r.email_claim, "groups_claim": r.groups_claim,
            "role_mapping": r.role_mapping, "default_role": r.default_role,
            "jit_provisioning": r.jit_provisioning, "enabled": r.enabled,
            "has_client_secret": bool(r.client_secret),
            "has_saml_certificate": bool(r.saml_certificate),
            "created_at": r.created_at}


def _get_provider_or_404(db: Session, tenant_id: str, provider_id: str) -> IdentityProvider:
    row = db.scalar(select(IdentityProvider).where(
        IdentityProvider.id == provider_id, IdentityProvider.tenant_id == tenant_id))
    if row is None:
        raise AppError("Identity provider not found", 404, "not_found")
    return row


@router.get("/admin/{tenant_id}/providers")
def list_providers(tenant_id: str, db: Session = Depends(get_session),
                   principal: Principal = Depends(get_current_principal)) -> list[dict]:
    resolve_tenant_scope(principal, tenant_id)
    rows = db.scalars(select(IdentityProvider).where(
        IdentityProvider.tenant_id == tenant_id)).all()
    return [_provider_out(r) for r in rows]


@router.post("/admin/{tenant_id}/providers", status_code=201)
def create_provider(
    tenant_id: str, payload: IdpCreate,
    db: Session = Depends(get_session),
    principal: Principal = Depends(get_current_principal),
) -> dict:
    _require_tenant_admin(principal, tenant_id)
    slug = validate_slug(payload.slug, what="provider slug")
    _validate_role_fields(payload.default_role, payload.role_mapping)

    if db.scalar(select(IdentityProvider).where(
            IdentityProvider.tenant_id == tenant_id, IdentityProvider.slug == slug)):
        raise AppError(f"A provider with slug '{slug}' already exists", 409,
                       "slug_conflict")

    row = IdentityProvider(
        tenant_id=tenant_id, slug=slug, display_name=payload.display_name.strip(),
        protocol=payload.protocol, issuer=payload.issuer.strip(),
        client_id=payload.client_id.strip(), client_secret=payload.client_secret,
        discovery_url=payload.discovery_url.strip(),
        authorize_url=payload.authorize_url.strip(), token_url=payload.token_url.strip(),
        jwks_url=payload.jwks_url.strip(),
        scopes=payload.scopes.strip() or "openid email profile",
        saml_sso_url=payload.saml_sso_url.strip(),
        saml_certificate=payload.saml_certificate,
        saml_email_attribute=payload.saml_email_attribute.strip(),
        saml_groups_attribute=payload.saml_groups_attribute.strip(),
        email_claim=payload.email_claim.strip() or "email",
        groups_claim=payload.groups_claim.strip() or "groups",
        role_mapping=payload.role_mapping, default_role=payload.default_role,
        jit_provisioning=payload.jit_provisioning, enabled=payload.enabled)
    db.add(row)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise AppError(f"A provider with slug '{slug}' already exists", 409,
                       "slug_conflict")
    db.refresh(row)
    record_audit(
        service="tenant-service", action="idp.create", actor=principal.subject,
        actor_role=principal.role, tenant_id=tenant_id, target_type="identity_provider",
        target_id=row.id, status="success",
        detail={"slug": row.slug, "protocol": row.protocol})
    return _provider_out(row)


@router.patch("/admin/{tenant_id}/providers/{provider_id}")
def update_provider(
    tenant_id: str, provider_id: str, payload: IdpUpdate,
    db: Session = Depends(get_session),
    principal: Principal = Depends(get_current_principal),
) -> dict:
    _require_tenant_admin(principal, tenant_id)
    row = _get_provider_or_404(db, tenant_id, provider_id)

    fields = payload.model_dump(exclude_unset=True)
    if "slug" in fields:
        fields["slug"] = validate_slug(fields["slug"], what="provider slug")
    _validate_role_fields(fields.get("default_role", row.default_role),
                          fields.get("role_mapping", row.role_mapping))

    # An empty client_secret / saml_certificate means "leave it as it is": the field
    # is never sent to the client to begin with, so an empty string cannot mean
    # "clear this" without also meaning "I never looked at it" - see IdpUpdate.
    for secret_field in ("client_secret", "saml_certificate"):
        if fields.get(secret_field) == "":
            fields.pop(secret_field)

    trim_exempt = {"client_secret", "saml_certificate", "role_mapping",
                  "jit_provisioning", "enabled"}
    for key, value in fields.items():
        setattr(row, key, value.strip() if isinstance(value, str)
                and key not in trim_exempt else value)

    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise AppError("A provider with that slug already exists", 409, "slug_conflict")
    db.refresh(row)
    record_audit(
        service="tenant-service", action="idp.update", actor=principal.subject,
        actor_role=principal.role, tenant_id=tenant_id, target_type="identity_provider",
        target_id=row.id, status="success", detail={"fields": sorted(fields.keys())})
    return _provider_out(row)


@router.delete("/admin/{tenant_id}/providers/{provider_id}", status_code=204)
def delete_provider(
    tenant_id: str, provider_id: str,
    db: Session = Depends(get_session),
    principal: Principal = Depends(get_current_principal),
) -> None:
    _require_tenant_admin(principal, tenant_id)
    row = _get_provider_or_404(db, tenant_id, provider_id)
    slug = row.slug
    db.delete(row)
    db.commit()
    record_audit(
        service="tenant-service", action="idp.delete", actor=principal.subject,
        actor_role=principal.role, tenant_id=tenant_id, target_type="identity_provider",
        target_id=provider_id, status="success", detail={"slug": slug})


# ------------------------------------------------------------------- SAML 2.0
def saml_acs_url() -> str:
    """Where the IdP POSTs its assertion: the public console, via the gateway."""
    return saml.acs_url(settings.console_base_url)


def _saml_sp_entity() -> str:
    return saml.sp_entity_id(settings.console_base_url)


@router.get("/saml/metadata")
def saml_metadata() -> Response:
    """SP metadata for the bank's IAM team to import.

    Public on purpose: it contains no secret, and requiring a login to fetch the thing
    you need in order to configure logging in is a circle every IAM team has been round.
    """
    return Response(saml.sp_metadata(settings.console_base_url),
                    media_type="application/samlmetadata+xml")


@router.get("/saml/{tenant_slug}/{idp_slug}/start")
def saml_start(tenant_slug: str, idp_slug: str,
               db: Session = Depends(get_session)) -> RedirectResponse:
    """Begin an SP-initiated SAML sign-in."""
    tenant, idp = _find_provider(db, tenant_slug, idp_slug)
    if idp.protocol != "saml":
        raise AppError("This sign-in method is not SAML", 400, "wrong_protocol")
    if not idp.saml_sso_url or not idp.saml_certificate.strip():
        # Without the certificate nothing coming back could be verified, so starting the
        # flow would only lead the user to a failure they cannot act on.
        raise AppError(
            "This SAML provider is not fully configured (needs the IdP SSO URL and its "
            "signing certificate).", 502, "idp_misconfigured")

    request_id = saml.new_request_id()
    # Reuses the OIDC in-flight table: it holds exactly what is needed here - which
    # provider, which tenant, single-use, and an expiry. The request id goes in the
    # ``state`` column and is what InResponseTo is checked against.
    db.add(SsoLoginState(
        state=request_id, provider_id=idp.id, tenant_id=tenant.id, nonce="",
        code_verifier="", redirect_uri=saml_acs_url(),
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=STATE_TTL_MINUTES)))
    db.commit()

    return RedirectResponse(saml.authn_request(
        idp_sso_url=idp.saml_sso_url, sp_entity=_saml_sp_entity(),
        acs=saml_acs_url(), request_id=request_id, relay_state=request_id), 302)


@router.post("/saml/acs")
def saml_acs(request: Request,
             SAMLResponse: str = Form(default=""),
             RelayState: str = Form(default=""),
             db: Session = Depends(get_session)) -> RedirectResponse:
    """Assertion Consumer Service: receive and verify the IdP's assertion."""
    if not SAMLResponse:
        return _console_redirect(sso_error="No assertion was received.")

    row = db.get(SsoLoginState, RelayState) if RelayState else None
    if row is None or row.used_at is not None:
        # Unsolicited or replayed. An SP that accepts assertions it did not ask for is
        # open to login-CSRF: an attacker POSTs their own valid assertion and the victim
        # ends up signed in as the attacker, then files their work into the wrong account.
        return _console_redirect(
            sso_error="This sign-in is no longer valid. Please start again.")
    if row.expires_at and row.expires_at.replace(
            tzinfo=row.expires_at.tzinfo or timezone.utc) < datetime.now(timezone.utc):
        return _console_redirect(sso_error="This sign-in took too long. Please try again.")

    row.used_at = datetime.now(timezone.utc)
    db.commit()

    idp = db.get(IdentityProvider, row.provider_id)
    tenant = db.get(Tenant, row.tenant_id)
    if idp is None or tenant is None or not idp.enabled or idp.protocol != "saml":
        return _console_redirect(sso_error="This sign-in method is no longer available.")

    try:
        identity = saml.verify_response(
            SAMLResponse,
            idp_certificate=idp.saml_certificate,
            idp_entity_id=idp.issuer,
            sp_entity_id_=_saml_sp_entity(),
            expected_acs=saml_acs_url(),
            expected_request_id=row.state)
    except saml.SamlError as exc:
        record_audit(
            service="tenant-service", action="auth.sso_failed", actor="(saml)",
            tenant_id=tenant.id, status="failure",
            detail={"provider": idp.slug, "code": exc.code, "protocol": "saml"})
        return _console_redirect(sso_error=exc.message)

    # ---- replay ------------------------------------------------------------------
    # The signature proves the assertion is genuine; it says nothing about whether this
    # is the first time it has been presented.
    seen = SamlAssertionSeen(
        tenant_id=tenant.id, provider_id=idp.id, assertion_id=identity.assertion_id,
        expires_at=identity.not_on_or_after
        or datetime.now(timezone.utc) + saml.MAX_ASSERTION_LIFETIME)
    db.add(seen)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        record_audit(
            service="tenant-service", action="auth.sso_failed", actor=identity.name_id,
            tenant_id=tenant.id, status="failure",
            detail={"provider": idp.slug, "code": "assertion_replayed",
                    "protocol": "saml"})
        return _console_redirect(
            sso_error="This sign-in response has already been used.")

    email = (identity.first(idp.saml_email_attribute) if idp.saml_email_attribute
             else "") or identity.name_id
    email = email.strip().lower()
    if "@" not in email:
        record_audit(
            service="tenant-service", action="auth.sso_failed", actor="(saml)",
            tenant_id=tenant.id, status="failure",
            detail={"provider": idp.slug, "code": "no_email", "protocol": "saml"})
        return _console_redirect(
            sso_error="Your organisation's sign-in did not provide an email address.")

    role = saml.map_role(identity, groups_attribute=idp.saml_groups_attribute,
                         role_mapping=idp.role_mapping, default_role=idp.default_role)
    if role not in ASSIGNABLE_TENANT_ROLES:
        record_audit(
            service="tenant-service", action="auth.sso_failed", actor=email,
            tenant_id=tenant.id, status="failure",
            detail={"provider": idp.slug, "reason": "unassignable_role", "role": role})
        return _console_redirect(
            sso_error="Your organisation's sign-in mapped you to a role this platform "
                      "cannot grant. Contact your administrator.")

    how = "group" if identity.attributes.get(idp.saml_groups_attribute) else "default"
    return _complete_sso(db, request, tenant, idp, email=email, role=role, how=how,
                         protocol="saml")
