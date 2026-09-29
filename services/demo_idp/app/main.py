"""A minimal OIDC provider, for demonstrations and for testing the real client path.

This exists so the federated sign-in code is exercised against an actual authorisation
code flow - discovery, PKCE, an RS256-signed ID token, JWKS - rather than against a mock
that agrees with whatever the client happens to do. A mock would have let every one of the
validations in ``cp_common.oidc`` pass vacuously.

It is NOT an identity provider. There is no password check, no consent screen, no session
management and no account security of any kind: you pick a persona from a list and it signs
a token asserting you are that person. That is what makes it useful for a demonstration,
and exactly why it refuses to start unless someone has explicitly asked for it.

For a real tenant, configure that bank's own provider instead - see
``services/tenant_service/app/idp_models.py``. Nothing about this service is on that path.
"""
import os
import time
import uuid
from urllib.parse import urlencode

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import FastAPI, Form, HTTPException, Query
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

# A demo provider reachable by accident is a way into a tenant. Refusing to start unless
# explicitly enabled means it cannot be left running through oversight.
if os.environ.get("DEMO_IDP_ENABLED", "").lower() not in ("1", "true", "yes"):
    raise RuntimeError(
        "The demo identity provider is disabled. It issues identity tokens without "
        "checking any credential and must never run beside real tenant data. "
        "Set DEMO_IDP_ENABLED=true only for local demonstrations.")

ISSUER = os.environ.get("DEMO_IDP_ISSUER", "http://127.0.0.1:8086")
CLIENT_ID = os.environ.get("DEMO_IDP_CLIENT_ID", "frms-console")
CLIENT_SECRET = os.environ.get("DEMO_IDP_CLIENT_SECRET", "demo-secret")
KEY_ID = "demo-key-1"

# Ephemeral: a fresh keypair per start, so a token from a previous run cannot be replayed
# into this one and no private key is ever committed to the repository.
_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
_PRIVATE_PEM = _KEY.private_bytes(
    serialization.Encoding.PEM,
    serialization.PrivateFormat.PKCS8,
    serialization.NoEncryption(),
).decode()

app = FastAPI(title="Demo Identity Provider (NOT FOR PRODUCTION)", version="0.1.0")

#: Personas the demo signs for, with the sort of directory groups a bank would send.
#: The last one is deliberately unmapped, to demonstrate least-privilege fallback.
PERSONAS = [
    ("asha.nair@demo-bank.example.com", "Asha Nair", ["FRAUD-ANALYSTS"]),
    ("vikram.rao@demo-bank.example.com", "Vikram Rao", ["FRAUD-INVESTIGATORS"]),
    ("meera.iyer@demo-bank.example.com", "Meera Iyer", ["FRAUD-RISK-MANAGERS"]),
    ("sanjay.menon@demo-bank.example.com", "Sanjay Menon", ["AML-PRINCIPAL-OFFICER"]),
    ("latha.k@demo-bank.example.com", "Latha Krishnan", ["COMPLIANCE-SUPERVISORS"]),
    ("r.subramanian@demo-bank.example.com", "R. Subramanian", ["BOARD"]),
    ("unmapped@demo-bank.example.com", "Unmapped Person", ["SOME-OTHER-GROUP"]),
]

_CODES: dict[str, dict] = {}


@app.get("/.well-known/openid-configuration")
def discovery() -> dict:
    return {
        "issuer": ISSUER,
        "authorization_endpoint": ISSUER + "/authorize",
        "token_endpoint": ISSUER + "/token",
        "jwks_uri": ISSUER + "/jwks.json",
        "response_types_supported": ["code"],
        "subject_types_supported": ["public"],
        "id_token_signing_alg_values_supported": ["RS256"],
        "scopes_supported": ["openid", "email", "profile", "groups"],
        "code_challenge_methods_supported": ["S256"],
        "claims_supported": ["sub", "email", "name", "groups"],
    }


@app.get("/jwks.json")
def jwks() -> dict:
    from jwt.algorithms import RSAAlgorithm

    key = RSAAlgorithm.to_jwk(_KEY.public_key(), as_dict=True)
    key.update({"kid": KEY_ID, "use": "sig", "alg": "RS256"})
    return {"keys": [key]}


def _persona_button(email: str, name: str, groups: list[str], redirect_uri: str,
                    state: str, nonce: str, challenge: str) -> str:
    joined = " ".join(groups)
    shown = " / ".join(groups)
    return (
        '<form method="post" action="/authorize" class="row">'
        '<input type="hidden" name="email" value="' + email + '">'
        '<input type="hidden" name="name" value="' + name + '">'
        '<input type="hidden" name="groups" value="' + joined + '">'
        '<input type="hidden" name="redirect_uri" value="' + redirect_uri + '">'
        '<input type="hidden" name="state" value="' + state + '">'
        '<input type="hidden" name="nonce" value="' + nonce + '">'
        '<input type="hidden" name="code_challenge" value="' + challenge + '">'
        '<button type="submit">'
        '<span class="who">' + name + '</span>'
        '<span class="mail">' + email + '</span>'
        '<span class="grp">' + shown + '</span>'
        "</button></form>"
    )


_PAGE_CSS = """
 body{font-family:system-ui,-apple-system,"Segoe UI",sans-serif;background:#0f1418;
   color:#e7efef;margin:0;display:flex;align-items:center;justify-content:center;
   min-height:100vh}
 .card{background:#151d20;border:1px solid #26343a;border-radius:14px;padding:26px;
   width:min(460px,92vw)}
 h1{font-size:17px;margin:0 0 4px}
 .sub{font-size:12px;color:#8fa3a6;margin:0}
 .warn{background:#3a2411;border:1px solid #7a4a12;color:#f0c07a;font-size:12px;
   padding:9px 12px;border-radius:8px;margin:14px 0 18px;line-height:1.5}
 .row button{width:100%;text-align:left;background:#1b2529;border:1px solid #2b393f;
   color:#e7efef;border-radius:10px;padding:10px 13px;margin-bottom:7px;cursor:pointer;
   display:flex;flex-direction:column;gap:2px;font:inherit}
 .row button:hover{border-color:#4FB0B6;background:#1e2c30}
 .who{font-weight:600;font-size:13.5px}
 .mail{font-size:11.5px;color:#8fa3a6}
 .grp{font-size:10.5px;color:#4FB0B6;letter-spacing:.04em}
"""


@app.get("/authorize", response_class=HTMLResponse)
def authorize(
    client_id: str = Query(...),
    redirect_uri: str = Query(...),
    state: str = Query(""),
    nonce: str = Query(""),
    scope: str = Query("openid"),
    response_type: str = Query("code"),
    code_challenge: str = Query(""),
    code_challenge_method: str = Query(""),
) -> HTMLResponse:
    """The persona picker that stands in for a real login page."""
    if client_id != CLIENT_ID:
        raise HTTPException(400, "unknown client")
    if response_type != "code":
        raise HTTPException(400, "only the authorization code flow is supported")

    rows = "".join(
        _persona_button(email, name, groups, redirect_uri, state, nonce, code_challenge)
        for email, name, groups in PERSONAS)

    return HTMLResponse(
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        "<title>Demo Identity Provider</title><style>" + _PAGE_CSS + "</style></head>"
        '<body><div class="card">'
        "<h1>Demo Identity Provider</h1>"
        '<p class="sub">Standing in for your bank\'s directory</p>'
        '<div class="warn"><b>Not a real identity provider.</b> No password is checked. '
        "Pick a person and a signed identity token will be issued for them. "
        "For demonstrations only.</div>" + rows + "</div></body></html>")


@app.post("/authorize")
def authorize_submit(
    email: str = Form(...),
    name: str = Form(""),
    groups: str = Form(""),
    redirect_uri: str = Form(...),
    state: str = Form(""),
    nonce: str = Form(""),
    code_challenge: str = Form(""),
) -> RedirectResponse:
    code = uuid.uuid4().hex
    _CODES[code] = {
        "email": email, "name": name, "groups": groups.split(),
        "nonce": nonce, "code_challenge": code_challenge,
        "redirect_uri": redirect_uri, "issued": time.time(),
    }
    return RedirectResponse(
        redirect_uri + "?" + urlencode({"code": code, "state": state}), status_code=303)


@app.post("/token")
def token(
    grant_type: str = Form(...),
    code: str = Form(...),
    redirect_uri: str = Form(...),
    client_id: str = Form(...),
    code_verifier: str = Form(""),
    client_secret: str = Form(""),
) -> JSONResponse:
    if grant_type != "authorization_code":
        return JSONResponse({"error": "unsupported_grant_type"}, status_code=400)
    if client_id != CLIENT_ID or (CLIENT_SECRET and client_secret != CLIENT_SECRET):
        return JSONResponse({"error": "invalid_client"}, status_code=401)

    # Single use. An authorisation code that can be spent twice is two sessions.
    entry = _CODES.pop(code, None)
    if entry is None:
        return JSONResponse({"error": "invalid_grant"}, status_code=400)
    if entry["redirect_uri"] != redirect_uri:
        return JSONResponse({"error": "invalid_grant"}, status_code=400)

    # PKCE is genuinely verified rather than accepted. A provider that ignored the
    # verifier would let a broken client pass its own tests.
    if entry["code_challenge"]:
        import base64
        import hashlib

        expected = base64.urlsafe_b64encode(
            hashlib.sha256(code_verifier.encode()).digest()).decode().rstrip("=")
        if expected != entry["code_challenge"]:
            return JSONResponse(
                {"error": "invalid_grant", "error_description": "PKCE check failed"},
                status_code=400)

    now = int(time.time())
    claims = {
        "iss": ISSUER, "aud": client_id, "sub": "demo|" + entry["email"],
        "iat": now, "exp": now + 300, "nonce": entry["nonce"],
        "email": entry["email"], "email_verified": True,
        "name": entry["name"], "groups": entry["groups"],
    }
    id_token = jwt.encode(claims, _PRIVATE_PEM, algorithm="RS256",
                          headers={"kid": KEY_ID})
    return JSONResponse({"access_token": uuid.uuid4().hex, "token_type": "Bearer",
                         "expires_in": 300, "id_token": id_token})


@app.get("/health", tags=["meta"])
def health() -> dict:
    return {"status": "ok", "service": "demo-idp", "issuer": ISSUER,
            "warning": "issues identity tokens without checking credentials"}
