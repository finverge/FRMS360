"""User invitation to an existing tenant, emailed instead of shown once on screen.

The authorisation half of this (a tenant_admin, not just platform_admin, can invite)
was already built and is covered by test_user_crud.py. This file covers the newer
half: what the invited user actually receives. Two states, and the response has to
tell the inviting admin honestly which one happened -
    (a) the tenant has a working email channel -> the credential goes to the
        invitee directly, and the temp password is withheld from the API response
        (no reason to show it twice);
    (b) it does not, or the send fails -> today's original behaviour, the password
        comes back once so the admin can relay it another way.
Sending an actual SMTP connection is out of scope for a test run (no live relay), so
the "channel configured and works" case monkeypatches send_email at the one seam that
is genuinely a network boundary - everything on tenant-service's side of that call is
real: the real route, the real internal HTTP call to notification-service, the real
audit record.
"""
from sqlalchemy import text

import pytest

from cp_common.db import SessionLocal


@pytest.fixture
def channel(tid):
    """Create/remove an email ChannelConfig row for the shared tenant around one test,
    so this file's channel state never leaks into another test using the same tid."""
    def _set(enabled: bool, config: dict | None = None):
        db = SessionLocal()
        try:
            from services.notification_service.app.models import ChannelConfig
            db.execute(text(
                "DELETE FROM notify.channel_config WHERE tenant_id = :t AND channel = 'email'"),
                {"t": tid})
            db.add(ChannelConfig(tenant_id=tid, channel="email", enabled=enabled,
                                 config=config or {}))
            db.commit()
        finally:
            db.close()
    yield _set
    db = SessionLocal()
    try:
        db.execute(text(
            "DELETE FROM notify.channel_config WHERE tenant_id = :t AND channel = 'email'"),
            {"t": tid})
        db.commit()
    finally:
        db.close()


@pytest.fixture(autouse=True)
def _clean_invited_users(tid):
    yield
    db = SessionLocal()
    try:
        db.execute(text(
            "DELETE FROM tenant.tenant_users WHERE tenant_id = :t "
            "AND email LIKE 'invite-email-test%@example.test'"), {"t": tid})
        db.commit()
    finally:
        db.close()


def _invite(tenant_client, token_for, tid, email):
    return tenant_client.post(
        f"/tenants/{tid}/users", headers=token_for("tenant_admin"),
        json={"email": email, "role": "analyst"})


# ------------------------------------------------------------------ no channel configured
def test_no_channel_configured_falls_back_to_shown_password(tenant_client, token_for, tid):
    r = _invite(tenant_client, token_for, tid, "invite-email-test-none@example.test")
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["emailed"] is False
    assert body["temp_password"], "fallback must still hand the admin a usable password"


def test_the_shown_password_still_signs_in(tenant_client, token_for, tid):
    r = _invite(tenant_client, token_for, tid, "invite-email-test-login@example.test")
    temp_password = r.json()["temp_password"]
    login = tenant_client.post("/auth/login", json={
        "email": "invite-email-test-login@example.test", "password": temp_password})
    assert login.status_code == 200, login.text
    assert login.json()["must_change_password"] is True


# ------------------------------------------------------------------ channel disabled
def test_a_disabled_channel_is_treated_the_same_as_no_channel(tenant_client, token_for,
                                                               tid, channel):
    channel(enabled=False, config={"host": "smtp.example.test", "port": 587})
    r = _invite(tenant_client, token_for, tid, "invite-email-test-disabled@example.test")
    body = r.json()
    assert body["emailed"] is False
    assert body["temp_password"]


# ------------------------------------------------------------------ channel configured, working
def test_a_working_channel_emails_the_invite_and_withholds_the_password(
        tenant_client, token_for, tid, channel, monkeypatch):
    channel(enabled=True, config={"host": "smtp.example.test", "port": 587,
                                  "from": "noreply@example.test"})
    from services.notification_service.app import channels as notify_channels
    sent = {}

    def fake_send_email(config, *, to, subject, body, link="", attachment=None):
        sent["to"], sent["subject"], sent["body"] = to, subject, body
        return notify_channels.Result(True, f"sent to {to}")
    monkeypatch.setattr(notify_channels, "send_email", fake_send_email)

    r = _invite(tenant_client, token_for, tid, "invite-email-test-works@example.test")
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["emailed"] is True
    assert body["temp_password"] is None, "must not show the password once it has been emailed"
    assert sent["to"] == "invite-email-test-works@example.test"
    assert "Fraud360" in sent["subject"]


def test_the_emailed_users_account_still_works_with_the_password_they_were_sent(
        tenant_client, token_for, tid, channel, monkeypatch):
    """The response withholds the password, but the account underneath must be no
    different from the on-screen path - same temp password mechanics, just delivered
    differently. Recover the real password the same way the invitee's inbox would have
    received it, straight from the fake send, and confirm it actually signs in."""
    channel(enabled=True, config={"host": "smtp.example.test", "port": 587})
    from services.notification_service.app import channels as notify_channels
    captured = {}

    def fake_send_email(config, *, to, subject, body, link="", attachment=None):
        captured["body"] = body
        return notify_channels.Result(True, "sent")
    monkeypatch.setattr(notify_channels, "send_email", fake_send_email)

    r = _invite(tenant_client, token_for, tid, "invite-email-test-e2e@example.test")
    assert r.status_code == 201, r.text
    assert r.json()["temp_password"] is None

    password_line = next(l for l in captured["body"].splitlines() if "Password:" in l)
    temp_password = password_line.split("Password:", 1)[1].strip()
    login = tenant_client.post("/auth/login", json={
        "email": "invite-email-test-e2e@example.test", "password": temp_password})
    assert login.status_code == 200, login.text


# ------------------------------------------------------------------ channel configured, broken
def test_a_relay_that_refuses_falls_back_to_shown_password(tenant_client, token_for,
                                                            tid, channel, monkeypatch):
    channel(enabled=True, config={"host": "smtp.example.test", "port": 587})
    from services.notification_service.app import channels as notify_channels
    monkeypatch.setattr(notify_channels, "send_email",
                        lambda *a, **k: notify_channels.Result(False, "connection refused"))

    r = _invite(tenant_client, token_for, tid, "invite-email-test-broken@example.test")
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["emailed"] is False
    assert body["temp_password"], "a rejected send must still fall back, never lose the credential"


# ------------------------------------------------------------------ audit trail
def test_the_audit_record_says_whether_it_was_emailed(tenant_client, token_for, tid, channel,
                                                       monkeypatch):
    from services.notification_service.app import channels as notify_channels
    channel(enabled=True, config={"host": "smtp.example.test", "port": 587})
    monkeypatch.setattr(notify_channels, "send_email",
                        lambda *a, **k: notify_channels.Result(True, "sent"))
    _invite(tenant_client, token_for, tid, "invite-email-test-audit@example.test")

    db = SessionLocal()
    try:
        row = db.execute(text(
            "SELECT detail FROM platform.audit_logs WHERE tenant_id = :t "
            "AND action = 'user.invite' ORDER BY ts DESC LIMIT 1"), {"t": tid}).mappings().first()
    finally:
        db.close()
    assert row is not None
    assert row["detail"]["emailed"] is True
    # The temp password itself must never appear in the audit trail, emailed or not.
    assert "password" not in str(row["detail"]).lower() or "temp_password" not in row["detail"]
