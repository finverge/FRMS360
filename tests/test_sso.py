"""Federated sign-in, driven against a real OIDC provider.

The demo IdP is a genuine authorisation-code provider: discovery document, PKCE
verification, RS256-signed ID token, published JWKS. That matters. Against a mock that
simply returns whatever the client asks for, every validation in ``cp_common.oidc`` would
pass without being exercised, and a missing signature check would look identical to a
working one.

The tests below therefore split into two groups: the flow works, and the flow refuses to
work when something is wrong with the token.
"""
import os

import pytest
from sqlalchemy import select, text

from cp_common.db import SessionLocal
from services.tenant_service.app.idp_models import IdentityProvider, SsoLoginState
from services.tenant_service.app.models import Tenant, TenantUser

os.environ.setdefault("DEMO_IDP_ENABLED", "true")

IDP_SLUG = "demo-directory"
#: What a bank's directory would send, mapped to what this platform grants.
ROLE_MAPPING = {
    "FRAUD-ANALYSTS": "analyst",
    "FRAUD-INVESTIGATORS": "investigator",
    "FRAUD-RISK-MANAGERS": "risk_manager",
    "AML-PRINCIPAL-OFFICER": "principal_officer",
    "COMPLIANCE-SUPERVISORS": "supervisor",
    "BOARD": "board",
}


@pytest.fixture(scope="module")
def idp_client():
    """The demo provider, in process."""
    from fastapi.testclient import TestClient

    from services.demo_idp.app.main import app
    with TestClient(app, base_url="http://demo-idp") as c:
        yield c


@pytest.fixture
def provider(seeded, idp_client, monkeypatch):
    """A tenant configured to federate to the demo provider.

    ``httpx`` calls from the OIDC client are routed to the in-process provider, so the
    real discovery / token / JWKS requests happen without a second process on a port.
    """
    import cp_common.oidc as oidc

    db = SessionLocal()
    try:
        tenant = db.scalar(select(Tenant).where(Tenant.slug == "testbank"))
        db.execute(text("DELETE FROM tenant.identity_providers WHERE tenant_id = :t"),
                   {"t": tenant.id})
        idp = IdentityProvider(
            tenant_id=tenant.id, slug=IDP_SLUG, display_name="Demo Directory",
            protocol="oidc", issuer="http://demo-idp", client_id="frms-console",
            client_secret="demo-secret",
            discovery_url="http://demo-idp/.well-known/openid-configuration",
            scopes="openid email profile groups", role_mapping=ROLE_MAPPING,
            default_role="analyst", jit_provisioning=True, enabled=True)
        db.add(idp)
        db.commit()
        idp_id, tenant_id = idp.id, tenant.id
    finally:
        db.close()

    oidc._DISCOVERY_CACHE.clear()

    class _Shim:
        @staticmethod
        def get(url, timeout=None, **kw):
            return idp_client.get(url.replace("http://demo-idp", ""))

        @staticmethod
        def post(url, data=None, timeout=None, headers=None, **kw):
            return idp_client.post(url.replace("http://demo-idp", ""), data=data,
                                   headers=headers)

    monkeypatch.setattr(oidc, "httpx", _Shim)

    # PyJWKClient fetches the key set over urllib, so point it at the same provider.
    import jwt.jwks_client as jwks_mod

    original = jwks_mod.PyJWKClient.fetch_data

    def _fetch(self):
        return idp_client.get(self.uri.replace("http://demo-idp", "")).json()

    monkeypatch.setattr(jwks_mod.PyJWKClient, "fetch_data", _fetch)

    yield {"tenant_id": tenant_id, "idp_id": idp_id, "slug": "testbank"}

    db = SessionLocal()
    try:
        db.execute(text("DELETE FROM tenant.identity_providers WHERE id = :i"),
                   {"i": idp_id})
        db.execute(text("DELETE FROM tenant.tenant_users WHERE email LIKE "
                        "'%@demo-bank.example.com'"))
        db.commit()
    finally:
        db.close()


def _sign_in(tenant_client, idp_client, provider, persona_email: str):
    """Walk the whole flow the way a browser would, and return the console redirect."""
    start = tenant_client.get(f"/auth/sso/{provider['slug']}/{IDP_SLUG}/start",
                              follow_redirects=False)
    assert start.status_code == 302, start.text
    authorize = start.headers["location"]

    page = idp_client.get(authorize.replace("http://demo-idp", ""))
    assert page.status_code == 200
    assert "Not a real identity provider" in page.text

    # Pick the persona the way the button would.
    from urllib.parse import parse_qs, urlparse
    q = parse_qs(urlparse(authorize).query)
    from services.demo_idp.app.main import PERSONAS
    email, name, groups = next(p for p in PERSONAS if p[0] == persona_email)

    submitted = idp_client.post("/authorize", data={
        "email": email, "name": name, "groups": " ".join(groups),
        "redirect_uri": q["redirect_uri"][0], "state": q["state"][0],
        "nonce": q["nonce"][0], "code_challenge": q["code_challenge"][0],
    }, follow_redirects=False)
    assert submitted.status_code == 303, submitted.text

    from urllib.parse import parse_qs as pq, urlparse as up
    cb = pq(up(submitted.headers["location"]).query)
    return tenant_client.get("/auth/sso/callback",
                             params={"code": cb["code"][0], "state": cb["state"][0]},
                             follow_redirects=False)


def _handoff(resp) -> dict:
    from urllib.parse import parse_qs, urlparse
    assert resp.status_code == 303, resp.text
    return {k: v[0] for k, v in parse_qs(urlparse(resp.headers["location"]).query).items()}


# ------------------------------------------------------------------- the flow works
def test_a_federated_user_can_sign_in(tenant_client, idp_client, provider):
    out = _handoff(_sign_in(tenant_client, idp_client, provider,
                            "asha.nair@demo-bank.example.com"))
    assert "sso_handoff" in out, out
    assert "sso_error" not in out

    # The handoff is a refresh token, exchanged immediately for a usable session.
    session = tenant_client.post("/auth/refresh",
                                 json={"refresh_token": out["sso_handoff"]})
    assert session.status_code == 200, session.text
    assert session.json()["role"] == "analyst"


def test_a_new_user_is_provisioned_from_the_directory(tenant_client, idp_client, provider):
    _handoff(_sign_in(tenant_client, idp_client, provider,
                      "vikram.rao@demo-bank.example.com"))
    db = SessionLocal()
    try:
        user = db.scalar(select(TenantUser).where(
            TenantUser.email == "vikram.rao@demo-bank.example.com"))
    finally:
        db.close()
    assert user is not None, "the directory user was not provisioned"
    assert user.role == "investigator", f"group mapping ignored: {user.role}"


def test_group_membership_decides_the_role(tenant_client, idp_client, provider):
    for email, expected in (("meera.iyer@demo-bank.example.com", "risk_manager"),
                            ("sanjay.menon@demo-bank.example.com", "principal_officer"),
                            ("r.subramanian@demo-bank.example.com", "board")):
        _handoff(_sign_in(tenant_client, idp_client, provider, email))
        db = SessionLocal()
        try:
            role = db.scalar(select(TenantUser.role).where(TenantUser.email == email))
        finally:
            db.close()
        assert role == expected, f"{email} mapped to {role}, expected {expected}"


def test_an_unmapped_group_gets_the_least_privileged_role(tenant_client, idp_client,
                                                          provider):
    """A directory group nobody has mapped must not confer authority by accident."""
    _handoff(_sign_in(tenant_client, idp_client, provider,
                      "unmapped@demo-bank.example.com"))
    db = SessionLocal()
    try:
        role = db.scalar(select(TenantUser.role).where(
            TenantUser.email == "unmapped@demo-bank.example.com"))
    finally:
        db.close()
    assert role == "analyst", f"unmapped user received {role}"


def test_a_role_change_in_the_directory_takes_effect(tenant_client, idp_client, provider):
    """The bank's directory is authoritative for who is who; a promotion there should
    not need a second, manual change here."""
    _handoff(_sign_in(tenant_client, idp_client, provider,
                      "asha.nair@demo-bank.example.com"))
    db = SessionLocal()
    try:
        db.execute(text("UPDATE tenant.tenant_users SET role = 'board' "
                        "WHERE email = 'asha.nair@demo-bank.example.com'"))
        db.commit()
    finally:
        db.close()

    _handoff(_sign_in(tenant_client, idp_client, provider,
                      "asha.nair@demo-bank.example.com"))
    db = SessionLocal()
    try:
        role = db.scalar(select(TenantUser.role).where(
            TenantUser.email == "asha.nair@demo-bank.example.com"))
    finally:
        db.close()
    assert role == "analyst", "the directory did not win over the stored role"


def test_a_federated_session_is_treated_as_second_factored(tenant_client, idp_client,
                                                           provider):
    """The bank's own IdP enforced its factors. Challenging again asks the same person
    for the same proof twice."""
    from services.tenant_service.app.auth_models import UserSession

    out = _handoff(_sign_in(tenant_client, idp_client, provider,
                            "latha.k@demo-bank.example.com"))
    from cp_common import hash_refresh_token
    db = SessionLocal()
    try:
        row = db.scalar(select(UserSession).where(
            UserSession.refresh_hash == hash_refresh_token(out["sso_handoff"])))
    finally:
        db.close()
    assert row is not None and row.mfa_satisfied is True


def test_providers_are_listed_without_leaking_configuration(tenant_client, provider):
    """The console needs this before anyone has signed in, so it must give away nothing
    an attacker could use to impersonate the platform to the bank's IdP."""
    body = tenant_client.get(f"/auth/sso/providers/{provider['slug']}").json()
    assert body["providers"][0]["slug"] == IDP_SLUG
    blob = str(body)
    for secret in ("demo-secret", "client_id", "frms-console", "token_endpoint"):
        assert secret not in blob, f"{secret} exposed on a public endpoint"


# ------------------------------------------------------------- the flow refuses
def test_a_replayed_callback_is_refused(tenant_client, idp_client, provider):
    """A state that has already been spent cannot mint a second session."""
    start = tenant_client.get(f"/auth/sso/{provider['slug']}/{IDP_SLUG}/start",
                              follow_redirects=False)
    from urllib.parse import parse_qs, urlparse
    q = parse_qs(urlparse(start.headers["location"]).query)
    submitted = idp_client.post("/authorize", data={
        "email": "asha.nair@demo-bank.example.com", "name": "Asha", "groups": "FRAUD-ANALYSTS",
        "redirect_uri": q["redirect_uri"][0], "state": q["state"][0],
        "nonce": q["nonce"][0], "code_challenge": q["code_challenge"][0],
    }, follow_redirects=False)
    cb = parse_qs(urlparse(submitted.headers["location"]).query)

    first = tenant_client.get("/auth/sso/callback",
                              params={"code": cb["code"][0], "state": cb["state"][0]},
                              follow_redirects=False)
    assert "sso_handoff" in _handoff(first)

    replay = tenant_client.get("/auth/sso/callback",
                               params={"code": cb["code"][0], "state": cb["state"][0]},
                               follow_redirects=False)
    assert "sso_error" in _handoff(replay), "a spent state was accepted twice"


def test_an_unknown_state_is_refused(tenant_client, provider):
    """A callback nobody asked for is the shape of a login-CSRF attempt."""
    out = _handoff(tenant_client.get("/auth/sso/callback",
                                     params={"code": "whatever", "state": "invented"},
                                     follow_redirects=False))
    assert "sso_error" in out


def test_the_id_token_signature_is_actually_checked(provider):
    """The single most important check. A token signed by the wrong key must fail."""
    import jwt
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.hazmat.primitives import serialization

    from cp_common.oidc import OidcError, ProviderEndpoints, validate_id_token

    attacker = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = attacker.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption()).decode()
    import time
    now = int(time.time())
    forged = jwt.encode({"iss": "http://demo-idp", "aud": "frms-console",
                         "sub": "demo|attacker", "iat": now, "exp": now + 300,
                         "email": "attacker@demo-bank.example.com", "nonce": "n"},
                        pem, algorithm="RS256", headers={"kid": "demo-key-1"})

    endpoints = ProviderEndpoints("http://demo-idp/authorize", "http://demo-idp/token",
                                  "http://demo-idp/jwks.json", "http://demo-idp")
    with pytest.raises(OidcError):
        validate_id_token(forged, endpoints=endpoints, client_id="frms-console",
                          nonce="n")


def test_pkce_is_verified_by_the_provider(idp_client):
    """Proves the provider is a real one. If it ignored the verifier, the client's PKCE
    implementation would be untested and a broken one would still pass."""
    from urllib.parse import parse_qs, urlparse

    submitted = idp_client.post("/authorize", data={
        "email": "asha.nair@demo-bank.example.com", "name": "Asha",
        "groups": "FRAUD-ANALYSTS", "redirect_uri": "http://console/cb",
        "state": "s", "nonce": "n",
        "code_challenge": "a-challenge-that-will-not-match",
    }, follow_redirects=False)
    code = parse_qs(urlparse(submitted.headers["location"]).query)["code"][0]

    r = idp_client.post("/token", data={
        "grant_type": "authorization_code", "code": code,
        "redirect_uri": "http://console/cb", "client_id": "frms-console",
        "client_secret": "demo-secret", "code_verifier": "wrong-verifier"})
    assert r.status_code == 400
    assert "PKCE" in r.json().get("error_description", "")


def test_an_authorisation_code_cannot_be_spent_twice(idp_client):
    from urllib.parse import parse_qs, urlparse
    import base64
    import hashlib

    verifier = "a" * 64
    challenge = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    submitted = idp_client.post("/authorize", data={
        "email": "asha.nair@demo-bank.example.com", "name": "Asha",
        "groups": "FRAUD-ANALYSTS", "redirect_uri": "http://console/cb",
        "state": "s", "nonce": "n", "code_challenge": challenge,
    }, follow_redirects=False)
    code = parse_qs(urlparse(submitted.headers["location"]).query)["code"][0]

    body = {"grant_type": "authorization_code", "code": code,
            "redirect_uri": "http://console/cb", "client_id": "frms-console",
            "client_secret": "demo-secret", "code_verifier": verifier}
    assert idp_client.post("/token", data=body).status_code == 200
    assert idp_client.post("/token", data=body).status_code == 400


# ----------------------------------------------------------------- the demo guard
def test_the_demo_provider_refuses_to_run_unless_asked(monkeypatch):
    """It signs identity tokens without checking any credential, so it must not be
    possible to leave it running beside real tenant data by oversight."""
    import importlib
    import sys

    monkeypatch.setenv("DEMO_IDP_ENABLED", "")
    sys.modules.pop("services.demo_idp.app.main", None)
    with pytest.raises(RuntimeError) as err:
        importlib.import_module("services.demo_idp.app.main")
    assert "without checking any credential" in str(err.value)

    monkeypatch.setenv("DEMO_IDP_ENABLED", "true")
    sys.modules.pop("services.demo_idp.app.main", None)
    importlib.import_module("services.demo_idp.app.main")


def test_the_browser_is_sent_back_to_the_console_not_to_a_service(provider):
    """Found in a browser, not by these tests.

    The redirect_uri was built with request.url_for(), which resolves to whichever host
    served the request. Behind the gateway that is tenant-service on its internal port,
    so the identity provider sent the browser to 127.0.0.1:8081 and it landed on a JSON
    404 instead of the console. A browser cannot reach an internal service address, and
    in a real deployment it must not be able to.
    """
    from cp_common import settings
    from services.tenant_service.app.routes.sso import callback_url

    url = callback_url()
    assert url.startswith(settings.console_base_url.rstrip("/")), \
        f"callback points somewhere other than the console origin: {url}"
    assert url.endswith("/api/auth/sso/callback"), \
        "the callback must go through the gateway path the browser can reach"
    for internal in (settings.tenant_service_url, settings.analytics_service_url,
                     settings.config_service_url):
        assert not url.startswith(internal.rstrip("/")), \
            f"callback points at an internal service address: {url}"


def test_the_final_handoff_is_absolute(provider):
    """A relative redirect resolves against whoever served the callback."""
    from cp_common import settings
    from services.tenant_service.app.routes.sso import _console_redirect

    target = _console_redirect(sso_handoff="x").headers["location"]
    assert target.startswith(settings.console_base_url.rstrip("/") + "/?"), target
