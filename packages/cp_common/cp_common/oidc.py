"""OIDC authorisation-code client, with PKCE.

Deliberately small and dependency-light: discovery, a code exchange, and strict validation
of the resulting ID token. Everything here is a pure function over inputs so the checks can
be tested without a browser, an IdP, or a redirect.

The validations below are the ones that matter, and each is here because skipping it is a
known, exploited mistake:

* **Signature against the IdP's published keys.** An unverified ID token is a claim by
  whoever sent it, which is the attacker in every interesting case.
* **Issuer.** A correctly-signed token from the *wrong* IdP is still the wrong person.
* **Audience.** A token minted for a different client at the same IdP must not be
  replayable here.
* **Nonce.** Binds the token to the authorisation request this browser actually started.
* **Expiry, with a small skew allowance.** Clocks differ; hours do not.

PKCE is used even though this is a confidential client. It costs one hash and removes the
whole class of attacks where an authorisation code leaks through a redirect, a log, or
browser history.
"""
from __future__ import annotations

import base64
import hashlib
import secrets
from dataclasses import dataclass, field
from urllib.parse import urlencode

import httpx
import jwt
from jwt import PyJWKClient

#: Tolerated clock difference between us and the IdP.
LEEWAY_SECONDS = 60
#: Discovery documents change rarely; refetching per login would put the bank's IdP in the
#: hot path of every sign-in.
_DISCOVERY_CACHE: dict[str, tuple[float, dict]] = {}
_DISCOVERY_TTL = 900.0


class OidcError(Exception):
    """A federated sign-in that cannot be trusted, with a reason fit to show a user."""

    def __init__(self, message: str, code: str = "sso_failed"):
        super().__init__(message)
        self.message, self.code = message, code


@dataclass
class ProviderEndpoints:
    authorize_url: str
    token_url: str
    jwks_url: str
    issuer: str


@dataclass
class Claims:
    subject: str
    email: str
    name: str = ""
    groups: list[str] = field(default_factory=list)
    raw: dict = field(default_factory=dict)


def discover(discovery_url: str, *, timeout: float = 10.0) -> ProviderEndpoints:
    """Read a provider's well-known document."""
    import time

    now = time.monotonic()
    hit = _DISCOVERY_CACHE.get(discovery_url)
    if hit and now - hit[0] < _DISCOVERY_TTL:
        doc = hit[1]
    else:
        try:
            r = httpx.get(discovery_url, timeout=timeout)
            r.raise_for_status()
            doc = r.json()
        except Exception as exc:  # noqa: BLE001
            raise OidcError(
                "Could not reach your organisation's identity provider.",
                "idp_unreachable") from exc
        _DISCOVERY_CACHE[discovery_url] = (now, doc)

    missing = [k for k in ("authorization_endpoint", "token_endpoint", "jwks_uri",
                           "issuer") if not doc.get(k)]
    if missing:
        raise OidcError(
            "The identity provider's configuration is incomplete: missing " +
            ", ".join(missing), "idp_misconfigured")
    return ProviderEndpoints(doc["authorization_endpoint"], doc["token_endpoint"],
                             doc["jwks_uri"], doc["issuer"])


def new_pkce() -> tuple[str, str]:
    """(verifier, challenge) using S256."""
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(48)).decode().rstrip("=")
    digest = hashlib.sha256(verifier.encode()).digest()
    challenge = base64.urlsafe_b64encode(digest).decode().rstrip("=")
    return verifier, challenge


def authorize_url(endpoints: ProviderEndpoints, *, client_id: str, redirect_uri: str,
                  scopes: str, state: str, nonce: str, challenge: str) -> str:
    params = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "scope": scopes,
        "state": state,
        "nonce": nonce,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    }
    sep = "&" if "?" in endpoints.authorize_url else "?"
    return f"{endpoints.authorize_url}{sep}{urlencode(params)}"


def exchange_code(endpoints: ProviderEndpoints, *, code: str, client_id: str,
                  client_secret: str, redirect_uri: str, verifier: str,
                  timeout: float = 10.0) -> dict:
    data = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": redirect_uri,
        "client_id": client_id,
        "code_verifier": verifier,
    }
    if client_secret:
        data["client_secret"] = client_secret
    try:
        r = httpx.post(endpoints.token_url, data=data, timeout=timeout,
                       headers={"Accept": "application/json"})
    except Exception as exc:  # noqa: BLE001
        raise OidcError("Could not reach your organisation's identity provider.",
                        "idp_unreachable") from exc
    if r.status_code >= 400:
        raise OidcError("Your organisation's identity provider rejected the sign-in.",
                        "token_exchange_failed")
    body = r.json()
    if not body.get("id_token"):
        raise OidcError("The identity provider did not return an identity token.",
                        "no_id_token")
    return body


def validate_id_token(id_token: str, *, endpoints: ProviderEndpoints, client_id: str,
                      nonce: str, email_claim: str = "email",
                      groups_claim: str = "groups") -> Claims:
    """Verify the token and pull out the claims we act on.

    Every failure here is deliberately reported as one generic message to the user. The
    distinction between "wrong audience" and "bad signature" is useful in a log and is
    reconnaissance in a browser.
    """
    try:
        signing_key = PyJWKClient(endpoints.jwks_url).get_signing_key_from_jwt(id_token)
        claims = jwt.decode(
            id_token,
            signing_key.key,
            algorithms=["RS256", "RS512", "ES256"],
            audience=client_id,
            issuer=endpoints.issuer,
            leeway=LEEWAY_SECONDS,
            options={"require": ["exp", "iat", "iss", "aud", "sub"]},
        )
    except Exception as exc:  # noqa: BLE001
        raise OidcError("Your organisation's sign-in could not be verified.",
                        "invalid_id_token") from exc

    # Checked explicitly: a token replayed from a different authorisation request would
    # otherwise pass every signature and issuer test above.
    if nonce and claims.get("nonce") != nonce:
        raise OidcError("Your organisation's sign-in could not be verified.",
                        "nonce_mismatch")

    email = (claims.get(email_claim) or "").strip().lower()
    if not email:
        raise OidcError(
            f"Your identity provider did not send an email address (claim "
            f"'{email_claim}'). Ask your administrator to include it in the token.",
            "no_email_claim")

    groups = claims.get(groups_claim) or []
    if isinstance(groups, str):
        groups = [g.strip() for g in groups.replace(",", " ").split() if g.strip()]

    return Claims(subject=str(claims.get("sub", "")), email=email,
                  name=str(claims.get("name", "")), groups=[str(g) for g in groups],
                  raw=claims)


def map_role(groups: list[str], mapping: dict, default_role: str) -> tuple[str, str]:
    """Resolve a platform role from the IdP's groups. Returns (role, how it was decided).

    Matching is case-insensitive because directories are inconsistent about it, and the
    *first* configured mapping wins rather than the most privileged: a deterministic
    answer an administrator can predict beats a clever one they cannot.
    """
    lowered = {g.lower() for g in groups}
    for group, role in (mapping or {}).items():
        if group.lower() in lowered:
            return role, f"group '{group}'"
    return default_role, "default (no mapped group)"
