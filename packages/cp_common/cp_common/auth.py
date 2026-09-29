"""Auth dependencies: decode the bearer token into a Principal and enforce roles.

Roles:
  - ``platform_admin``: your staff; may act across all tenants.
  - ``tenant_admin``:   a bank's administrator; scoped to their own tenant_id.
"""
from dataclasses import dataclass

from fastapi import Depends, Header, HTTPException, status

from .security import decode_token
from .settings import settings


@dataclass
class Principal:
    subject: str
    role: str
    tenant_id: str | None
    scope: str = "full"

    @property
    def is_platform_admin(self) -> bool:
        return self.role == "platform_admin"


def _decode(authorization: str | None) -> Principal:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Missing bearer token")
    token = authorization.split(" ", 1)[1]
    try:
        claims = decode_token(token)
    except Exception:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid or expired token")
    return Principal(
        subject=claims.get("sub", ""),
        role=claims.get("role", ""),
        tenant_id=claims.get("tenant_id"),
        # Tokens issued before this field existed are treated as full access.
        scope=claims.get("scope", "full"),
    )


#: Scopes that exist only to complete an authentication step. A token carrying one of
#: these has proved something partial - a password, but not possession of the second
#: factor - and must not reach anything else.
LIMITED_SCOPES = {
    "password_reset": "Password change required before continuing",
    "mfa_pending": "Second factor required before continuing",
}

#: Scopes carried by machine credentials. These are not partial authentications - the
#: caller is fully authenticated - but they are deliberately narrow, so a token issued to
#: a payment switch cannot read a case, unmask a customer or change a threshold. Every
#: endpoint outside intake and decisioning refuses them, which is what makes a leaked
#: intake key survivable.
MACHINE_SCOPES = {"ingest", "decide", "ingest+decide"}


def is_machine(principal: "Principal") -> bool:
    return principal.scope in MACHINE_SCOPES


def machine_may(principal: "Principal", capability: str) -> bool:
    """Whether a machine token covers a capability. Humans are unaffected."""
    if not is_machine(principal):
        return True
    return capability in principal.scope.split("+")


def get_current_principal(authorization: str | None = Header(default=None)) -> Principal:
    """Standard dependency: rejects tokens issued mid-authentication.

    It also rejects **machine tokens**. Those are admitted only by the endpoints that
    explicitly opt in via ``require_machine_scope``, so a credential issued to a payment
    switch reaches intake and decisioning and nothing else. Defaulting the other way -
    admitting machine tokens everywhere and blocking case-by-case - would mean every new
    endpoint is exposed until someone remembers to close it.
    """
    principal = _decode(authorization)
    message = LIMITED_SCOPES.get(principal.scope)
    if message:
        raise HTTPException(status.HTTP_403_FORBIDDEN, message)
    if is_machine(principal):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "This is a machine credential. It may submit transactions and request "
            "decisions; it cannot reach case, configuration or customer data.")
    return principal


def require_machine_scope(capability: str):
    """Admit a machine token carrying ``capability`` - and a human, unchanged.

    Used by intake and decisioning. A human with the right role keeps working, so this
    widens rather than replaces the existing check.

    The tenant's lifecycle standing is checked here rather than in each route, because
    this is the single door every machine caller comes through. A suspension that only
    stopped the console login left the switch posting and the channel deciding, which is
    the traffic that actually matters — see ``tenant_status``.
    """

    def dependency(authorization: str | None = Header(default=None)) -> Principal:
        principal = _decode(authorization)
        message = LIMITED_SCOPES.get(principal.scope)
        if message:
            raise HTTPException(status.HTTP_403_FORBIDDEN, message)
        if is_machine(principal) and not machine_may(principal, capability):
            raise HTTPException(
                status.HTTP_403_FORBIDDEN,
                f"This machine credential is scoped '{principal.scope}' and cannot "
                f"{capability}.")
        if is_machine(principal) and principal.tenant_id:
            from .tenant_status import standing  # local: avoids an import cycle
            st = standing(principal.tenant_id)
            if not st.served:
                raise HTTPException(status.HTTP_403_FORBIDDEN, st.reason)
        return principal

    return dependency


def get_principal_any_scope(authorization: str | None = Header(default=None)) -> Principal:
    """For the change-password endpoint, which must accept reset-scoped tokens."""
    return _decode(authorization)


def require_role(*roles: str):
    def dependency(principal: Principal = Depends(get_current_principal)) -> Principal:
        if principal.role not in roles:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Insufficient role")
        return principal

    return dependency


def require_internal_key(x_internal_key: str | None = Header(default=None)) -> None:
    """Guards service-to-service endpoints used during tenant onboarding."""
    if x_internal_key != settings.internal_api_key:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid internal key")
