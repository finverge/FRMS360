"""Authentication controls: second factor, lockout, and session lifetime.

These are the controls a bank's information-security review actually tests, so each one
is checked for the attack it exists to stop rather than for the happy path alone.
"""
import time

import pyotp
import pytest
from sqlalchemy import select, text

from cp_common import hash_refresh_token, settings
from cp_common.db import SessionLocal
from services.tenant_service.app.auth_models import UserSession
from services.tenant_service.app.models import TenantUser


def _login(client, email, password):
    return client.post("/auth/login", json={"email": email, "password": password})


def _unlock(email: str) -> None:
    db = SessionLocal()
    try:
        db.execute(text("UPDATE tenant.tenant_users SET failed_attempts = 0, "
                        "locked_until = NULL WHERE email = :e"), {"e": email})
        db.execute(text("UPDATE tenant.platform_users SET failed_attempts = 0, "
                        "locked_until = NULL WHERE email = :e"), {"e": email})
        db.commit()
    finally:
        db.close()


@pytest.fixture
def analyst(seeded):
    email = "analyst@test.local"
    yield email, seeded["user_password"]
    _unlock(email)
    db = SessionLocal()
    try:
        db.execute(text("UPDATE tenant.tenant_users SET mfa_enabled = false, "
                        "mfa_secret = '' WHERE email = :e"), {"e": email})
        db.execute(text("DELETE FROM tenant.mfa_backup_codes WHERE user_id IN "
                        "(SELECT id FROM tenant.tenant_users WHERE email = :e)"),
                   {"e": email})
        db.commit()
    finally:
        db.close()


# ------------------------------------------------------------------- sessions
def test_login_issues_an_access_and_a_refresh_token(tenant_client, analyst):
    email, pw = analyst
    body = _login(tenant_client, email, pw).json()
    assert body["access_token"] and body["refresh_token"]
    assert body["expires_in"] == settings.jwt_expire_minutes * 60


def test_access_tokens_are_short_lived(tenant_client, analyst):
    """An access token cannot be revoked, so its lifetime is the window in which a
    disabled user can still act. Eight hours was far too long for that."""
    assert settings.jwt_expire_minutes <= 60, \
        "access tokens outlive any plausible revocation window"


def test_refresh_rotates_the_token(tenant_client, analyst):
    email, pw = analyst
    first = _login(tenant_client, email, pw).json()
    second = tenant_client.post("/auth/refresh",
                                json={"refresh_token": first["refresh_token"]})
    assert second.status_code == 200, second.text
    assert second.json()["refresh_token"] != first["refresh_token"], \
        "the refresh token was reused rather than rotated"


def test_replaying_a_refresh_token_ends_every_session(tenant_client, analyst):
    """A token presented after it was exchanged means two parties hold it.

    Serving either would be worse than serving neither, so the whole rotation chain is
    ended and both must sign in again.
    """
    email, pw = analyst
    first = _login(tenant_client, email, pw).json()
    rotated = tenant_client.post("/auth/refresh",
                                 json={"refresh_token": first["refresh_token"]}).json()

    replay = tenant_client.post("/auth/refresh",
                                json={"refresh_token": first["refresh_token"]})
    assert replay.status_code == 401
    assert replay.json()["error"]["code"] == "token_reuse"

    # The successor is dead too - that is the point.
    after = tenant_client.post("/auth/refresh",
                               json={"refresh_token": rotated["refresh_token"]})
    assert after.status_code == 401, "the stolen chain was still usable"


def test_logout_ends_the_session(tenant_client, analyst):
    email, pw = analyst
    body = _login(tenant_client, email, pw).json()
    assert tenant_client.post("/auth/logout",
                              json={"refresh_token": body["refresh_token"]}).status_code == 200
    again = tenant_client.post("/auth/refresh",
                               json={"refresh_token": body["refresh_token"]})
    assert again.status_code == 401


def test_a_user_can_see_and_end_all_their_sessions(tenant_client, analyst):
    email, pw = analyst
    a = _login(tenant_client, email, pw).json()
    _login(tenant_client, email, pw)
    h = {"Authorization": "Bearer " + a["access_token"]}

    listed = tenant_client.get("/auth/sessions", headers=h).json()
    assert len(listed) >= 2, "a user cannot notice a session they did not start"

    ended = tenant_client.post("/auth/logout-all", headers=h).json()
    assert ended["sessions_ended"] >= 2
    assert tenant_client.post("/auth/refresh",
                              json={"refresh_token": a["refresh_token"]}).status_code == 401


def test_an_unknown_refresh_token_is_refused(tenant_client):
    r = tenant_client.post("/auth/refresh", json={"refresh_token": "not-a-real-token"})
    assert r.status_code == 401


def test_refresh_tokens_are_stored_hashed(tenant_client, analyst):
    """A stolen database must not yield usable session tokens."""
    email, pw = analyst
    body = _login(tenant_client, email, pw).json()
    raw = body["refresh_token"]
    db = SessionLocal()
    try:
        stored = db.scalar(select(UserSession).where(
            UserSession.refresh_hash == hash_refresh_token(raw)))
        assert stored is not None, "session not found by hash"
        assert raw not in stored.refresh_hash
        plain = db.execute(text(
            "SELECT COUNT(*) FROM tenant.user_sessions WHERE refresh_hash = :raw"),
            {"raw": raw}).scalar()
        assert plain == 0, "the raw refresh token is in the database"
    finally:
        db.close()


# ------------------------------------------------------------------- lockout
def test_repeated_failures_lock_the_account(tenant_client, analyst):
    email, _pw = analyst
    last = None
    for _ in range(settings.max_failed_attempts):
        last = _login(tenant_client, email, "WrongPassword#1")
    assert last.status_code == 423, f"never locked: {last.status_code}"
    assert last.json()["error"]["code"] == "account_locked"


def test_a_locked_account_refuses_even_the_right_password(tenant_client, analyst):
    email, pw = analyst
    for _ in range(settings.max_failed_attempts):
        _login(tenant_client, email, "WrongPassword#1")
    blocked = _login(tenant_client, email, pw)
    assert blocked.status_code == 423, "lockout did not hold against the real password"


def test_a_successful_login_clears_the_failure_count(tenant_client, analyst):
    """Someone who mistypes twice a day should not creep toward a lockout."""
    email, pw = analyst
    _login(tenant_client, email, "WrongPassword#1")
    _login(tenant_client, email, "WrongPassword#1")
    assert _login(tenant_client, email, pw).status_code == 200

    db = SessionLocal()
    try:
        n = db.scalar(select(TenantUser.failed_attempts).where(TenantUser.email == email))
    finally:
        db.close()
    assert n == 0, f"failure count survived a successful login: {n}"


def test_an_unknown_address_is_indistinguishable_from_a_wrong_password(tenant_client):
    """Telling an attacker which addresses exist is free reconnaissance."""
    unknown = _login(tenant_client, "nobody@nowhere.test", "whatever")
    known = _login(tenant_client, "analyst@test.local", "DefinitelyWrong#9")
    assert unknown.status_code == known.status_code == 401
    assert unknown.json()["error"]["message"] == known.json()["error"]["message"]
    _unlock("analyst@test.local")


# -------------------------------------------------------------- second factor
def _enrol(client, token) -> tuple[str, list[str]]:
    h = {"Authorization": "Bearer " + token}
    start = client.post("/auth/mfa/enrol", headers=h)
    assert start.status_code == 200, start.text
    secret = start.json()["secret"]
    done = client.post("/auth/mfa/activate", headers=h,
                       json={"code": pyotp.TOTP(secret).now()})
    assert done.status_code == 200, done.text
    return secret, done.json()["backup_codes"]


def test_enrolment_requires_a_working_code_before_switching_on(tenant_client, analyst):
    """Enabling on trust would lock out anyone whose device was misconfigured."""
    email, pw = analyst
    token = _login(tenant_client, email, pw).json()["access_token"]
    h = {"Authorization": "Bearer " + token}
    tenant_client.post("/auth/mfa/enrol", headers=h)
    bad = tenant_client.post("/auth/mfa/activate", headers=h, json={"code": "000000"})
    assert bad.status_code == 401
    assert tenant_client.get("/auth/mfa/status", headers=h).json()["enabled"] is False


def test_enrolment_returns_a_scannable_qr(tenant_client, analyst):
    email, pw = analyst
    token = _login(tenant_client, email, pw).json()["access_token"]
    body = tenant_client.post("/auth/mfa/enrol",
                              headers={"Authorization": "Bearer " + token}).json()
    assert body["otpauth_uri"].startswith("otpauth://totp/")
    assert body["qr_svg"].lstrip().startswith("<?xml") or "<svg" in body["qr_svg"]


def test_a_password_alone_no_longer_grants_access(tenant_client, analyst):
    """The whole point of the control: a stolen password is not enough."""
    email, pw = analyst
    token = _login(tenant_client, email, pw).json()["access_token"]
    secret, _codes = _enrol(tenant_client, token)

    body = _login(tenant_client, email, pw).json()
    assert body["mfa_required"] is True
    assert body.get("refresh_token") is None, "a session opened without the second factor"

    # And the interim token can reach nothing.
    blocked = tenant_client.get("/auth/sessions",
                                headers={"Authorization": "Bearer " + body["access_token"]})
    assert blocked.status_code == 403


def test_the_second_factor_completes_the_login(tenant_client, analyst):
    email, pw = analyst
    token = _login(tenant_client, email, pw).json()["access_token"]
    secret, _ = _enrol(tenant_client, token)

    pending = _login(tenant_client, email, pw).json()["access_token"]
    done = tenant_client.post("/auth/mfa/verify",
                              headers={"Authorization": "Bearer " + pending},
                              json={"code": pyotp.TOTP(secret).now()})
    assert done.status_code == 200, done.text
    assert done.json()["refresh_token"], "no session opened after a valid code"


def test_a_recovery_code_works_once(tenant_client, analyst):
    email, pw = analyst
    token = _login(tenant_client, email, pw).json()["access_token"]
    _secret, codes = _enrol(tenant_client, token)

    pending = _login(tenant_client, email, pw).json()["access_token"]
    first = tenant_client.post("/auth/mfa/verify",
                               headers={"Authorization": "Bearer " + pending},
                               json={"code": codes[0]})
    assert first.status_code == 200, first.text

    pending2 = _login(tenant_client, email, pw).json()["access_token"]
    reuse = tenant_client.post("/auth/mfa/verify",
                               headers={"Authorization": "Bearer " + pending2},
                               json={"code": codes[0]})
    assert reuse.status_code == 401, "a recovery code was accepted twice"


def test_failed_codes_count_toward_the_same_lockout(tenant_client, analyst):
    """An attacker holding the password must not get an unlimited budget against the
    six digits that are actually protecting the account."""
    email, pw = analyst
    token = _login(tenant_client, email, pw).json()["access_token"]
    _enrol(tenant_client, token)

    last = None
    for _ in range(settings.max_failed_attempts):
        pending = _login(tenant_client, email, pw).json()["access_token"]
        last = tenant_client.post("/auth/mfa/verify",
                                  headers={"Authorization": "Bearer " + pending},
                                  json={"code": "000000"})
    assert last.status_code == 423, f"second-factor guessing was never rate-limited: {last.status_code}"


def test_a_mandated_factor_cannot_be_switched_off(tenant_client, analyst, tid):
    """A role the bank mandates a factor for must not be able to opt out of it."""
    email, pw = analyst
    db = SessionLocal()
    try:
        db.execute(text("UPDATE tenant.tenants SET mfa_policy = 'privileged' "
                        "WHERE id = :t"), {"t": tid})
        db.commit()
    finally:
        db.close()
    token = _login(tenant_client, email, pw).json()["access_token"]
    secret, _ = _enrol(tenant_client, token)

    pending = _login(tenant_client, email, pw).json()["access_token"]
    full = tenant_client.post("/auth/mfa/verify",
                              headers={"Authorization": "Bearer " + pending},
                              json={"code": pyotp.TOTP(secret).now()}).json()["access_token"]

    off = tenant_client.post("/auth/mfa/disable",
                             headers={"Authorization": "Bearer " + full},
                             json={"password": pw, "code": pyotp.TOTP(secret).now()})
    assert off.status_code == 403
    assert off.json()["error"]["code"] == "mfa_mandatory"

    db = SessionLocal()
    try:
        db.execute(text("UPDATE tenant.tenants SET mfa_policy = 'optional' "
                        "WHERE id = :t"), {"t": tid})
        db.commit()
    finally:
        db.close()


def test_platform_staff_always_require_a_second_factor():
    """The account that can reach every tenant is the one an outsourcing review asks
    about first."""
    from services.tenant_service.app import auth_service as svc

    db = SessionLocal()
    try:
        identity = svc.find_identity(db, "admin@finverge.local")
        if identity is None:
            pytest.skip("no platform user seeded")
        required, _ = svc.mfa_obligation(db, identity)
    finally:
        db.close()
    assert required is True


def test_limited_scopes_cannot_reach_the_api():
    from cp_common.auth import LIMITED_SCOPES
    assert "mfa_pending" in LIMITED_SCOPES and "password_reset" in LIMITED_SCOPES
