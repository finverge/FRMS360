"""BR-609: save/share views, schedule, and distribute exports to named recipients.

Saved views (SavedView) and the subscription CRUD/scheduling layer (ReportSubscription)
already existed; what did not was the "distribute" half - a subscription's ``email``
driver reported itself unconfigured no matter what, so a named recipient never received
anything. These tests cover the three things that changed: subscriptions have basic CRUD
coverage at all (they had none), the new internal send-through-a-tenant's-own-relay
endpoint on notification-service, and run_subscriptions.py actually calling it.
"""
import base64

import pytest
from sqlalchemy import text

from cp_common.db import SessionLocal


@pytest.fixture
def clean_channel(tid):
    def _wipe():
        db = SessionLocal()
        try:
            db.execute(text("DELETE FROM notify.channel_config WHERE tenant_id = :t"),
                       {"t": tid})
            db.commit()
        finally:
            db.close()
    _wipe()
    yield
    _wipe()


@pytest.fixture
def clean_subscriptions(tid):
    def _wipe():
        db = SessionLocal()
        try:
            db.execute(text("DELETE FROM analytics.report_subscriptions "
                            "WHERE tenant_id = :t"), {"t": tid})
            db.commit()
        finally:
            db.close()
    _wipe()
    yield
    _wipe()


# --------------------------------------------------------------------- CRUD
def test_a_subscription_can_be_saved_and_listed(analytics_client, token_for, tid,
                                                 clean_subscriptions):
    h = token_for("risk_manager")
    made = analytics_client.post(
        f"/analytics/{tid}/subscriptions", headers=h,
        json={"name": "weekly-cases", "entity": "case", "cadence": "weekly",
              "driver": "email", "recipients": "compliance@bank.example.com"})
    assert made.status_code == 201, made.text
    body = made.json()
    assert body["mine"] is True
    assert body["due_now"] is True, "a never-run subscription must be immediately due"

    listed = analytics_client.get(f"/analytics/{tid}/subscriptions", headers=h).json()
    assert any(s["name"] == "weekly-cases" for s in listed)


def test_posting_the_same_name_again_updates_rather_than_duplicates(
        analytics_client, token_for, tid, clean_subscriptions):
    h = token_for("risk_manager")
    payload = {"name": "dup-check", "entity": "case", "cadence": "daily"}
    first = analytics_client.post(f"/analytics/{tid}/subscriptions", headers=h,
                                  json=payload)
    second = analytics_client.post(f"/analytics/{tid}/subscriptions", headers=h,
                                   json={**payload, "cadence": "monthly"})
    assert first.json()["id"] == second.json()["id"]
    assert second.json()["cadence"] == "monthly"


def test_a_regular_user_sees_only_their_own_subscriptions(analytics_client, token_for,
                                                           tid, clean_subscriptions):
    analytics_client.post(f"/analytics/{tid}/subscriptions", headers=token_for("analyst"),
                          json={"name": "analyst-own", "entity": "alert"})
    analytics_client.post(f"/analytics/{tid}/subscriptions",
                          headers=token_for("risk_manager"),
                          json={"name": "rm-own", "entity": "alert"})
    mine = analytics_client.get(f"/analytics/{tid}/subscriptions",
                                headers=token_for("analyst")).json()
    names = {s["name"] for s in mine}
    assert "analyst-own" in names
    assert "rm-own" not in names


def test_a_tenant_admin_sees_every_subscription_in_the_tenant(analytics_client, token_for,
                                                               tid, clean_subscriptions):
    analytics_client.post(f"/analytics/{tid}/subscriptions", headers=token_for("analyst"),
                          json={"name": "someones-report", "entity": "alert"})
    seen = analytics_client.get(f"/analytics/{tid}/subscriptions",
                                headers=token_for("tenant_admin")).json()
    assert any(s["name"] == "someones-report" for s in seen)


def test_only_the_owner_or_an_admin_may_delete_a_subscription(analytics_client, token_for,
                                                               tid, clean_subscriptions):
    made = analytics_client.post(f"/analytics/{tid}/subscriptions",
                                 headers=token_for("risk_manager"),
                                 json={"name": "not-yours", "entity": "case"})
    sid = made.json()["id"]
    refused = analytics_client.delete(f"/analytics/{tid}/subscriptions/{sid}",
                                      headers=token_for("analyst"))
    assert refused.status_code == 403, refused.text
    assert refused.json()["error"]["code"] == "not_yours"

    ok = analytics_client.delete(f"/analytics/{tid}/subscriptions/{sid}",
                                 headers=token_for("risk_manager"))
    assert ok.status_code == 204


def test_options_lists_the_real_cadences_drivers_and_formats(analytics_client, token_for,
                                                              tid):
    opts = analytics_client.get(f"/analytics/{tid}/subscriptions/options",
                                headers=token_for("analyst")).json()
    assert set(opts["cadences"]) == {"daily", "weekly", "monthly"}
    assert set(opts["drivers"]) == {"spool", "email"}
    assert opts["formats"] == ["csv"]


# ------------------------------------------------- recipient parsing (pure)
def test_named_recipients_splits_on_comma_semicolon_and_newline():
    from scripts.run_subscriptions import _named_recipients

    got = _named_recipients("a@bank.example.com, b@bank.example.com;\nc@bank.example.com")
    assert got == ["a@bank.example.com", "b@bank.example.com", "c@bank.example.com"]


def test_named_recipients_dedupes_and_ignores_blanks():
    from scripts.run_subscriptions import _named_recipients

    assert _named_recipients("a@x.com, , a@x.com,  ") == ["a@x.com"]


def test_named_recipients_of_nothing_is_empty():
    from scripts.run_subscriptions import _named_recipients

    assert _named_recipients("") == []
    assert _named_recipients(None) == []


# -------------------------------------------------- channels.send_email attachment
def test_send_email_attaches_the_export_when_given_one(monkeypatch):
    from services.notification_service.app import channels

    captured = {}

    class _FakeSMTP:
        def __init__(self, host, port, timeout=None):
            captured["host"], captured["port"] = host, port

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def starttls(self):
            pass

        def send_message(self, message):
            captured["message"] = message

    monkeypatch.setattr(channels.smtplib, "SMTP", _FakeSMTP)

    result = channels.send_email(
        {"host": "relay.bank.example.com", "port": 587, "starttls": True},
        to="compliance@bank.example.com", subject="Scheduled report", body="see attached",
        attachment=("cases.csv", b"id,status\n1,open\n", "text/csv"))

    assert result.ok, result.detail
    msg = captured["message"]
    parts = list(msg.iter_attachments())
    assert len(parts) == 1
    assert parts[0].get_filename() == "cases.csv"
    assert parts[0].get_content().strip() == "id,status\n1,open"


def test_send_email_without_a_host_is_refused_before_touching_the_network():
    from services.notification_service.app import channels

    result = channels.send_email({}, to="x@y.com", subject="s", body="b")
    assert not result.ok
    assert "no SMTP host" in result.detail


# ------------------------------------------------- /internal/channels/send endpoint
def test_send_channel_message_reports_no_channel_configured(notification_client, tid,
                                                             clean_channel):
    from cp_common.settings import settings

    r = notification_client.post(
        "/internal/channels/send",
        headers={"x-internal-key": settings.internal_api_key},
        json={"tenant_id": tid, "channel": "email", "to": "x@y.com",
              "subject": "s", "body": "b"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is False
    assert "no email channel configured" in body["detail"]


def test_send_channel_message_refuses_a_non_email_channel(notification_client, tid):
    from cp_common.settings import settings

    r = notification_client.post(
        "/internal/channels/send",
        headers={"x-internal-key": settings.internal_api_key},
        json={"tenant_id": tid, "channel": "webhook", "to": "x@y.com",
              "subject": "s", "body": "b"})
    assert r.status_code == 400, r.text
    assert r.json()["error"]["code"] == "unsupported_channel"


def test_send_channel_message_requires_the_internal_key(notification_client, tid):
    r = notification_client.post(
        "/internal/channels/send",
        json={"tenant_id": tid, "channel": "email", "to": "x@y.com",
              "subject": "s", "body": "b"})
    assert r.status_code == 401, r.text


def test_send_channel_message_decodes_and_forwards_the_attachment(
        notification_client, token_for, tid, clean_channel, monkeypatch):
    from cp_common.settings import settings
    from services.notification_service.app import channels

    notification_client.put(
        f"/notifications/{tid}/channels", headers=token_for("tenant_admin"),
        json={"channel": "email", "enabled": True,
              "config": {"host": "relay.bank.example.com"}})

    captured = {}

    def fake_send_email(config, *, to, subject, body, link="", attachment=None):
        captured["to"], captured["attachment"] = to, attachment
        return channels.Result(True, f"sent to {to}")

    monkeypatch.setattr(channels, "send_email", fake_send_email)

    content = b"id,status\n1,open\n"
    r = notification_client.post(
        "/internal/channels/send",
        headers={"x-internal-key": settings.internal_api_key},
        json={"tenant_id": tid, "channel": "email", "to": "compliance@bank.example.com",
              "subject": "Scheduled report", "body": "see attached",
              "attachment_filename": "cases.csv",
              "attachment_content_b64": base64.b64encode(content).decode(),
              "attachment_mime": "text/csv"})
    assert r.status_code == 200, r.text
    assert r.json()["ok"] is True
    assert captured["to"] == "compliance@bank.example.com"
    filename, decoded, mime = captured["attachment"]
    assert filename == "cases.csv"
    assert decoded == content
    assert mime == "text/csv"


# --------------------------------------------------------- run_subscriptions.deliver()
def test_deliver_spool_writes_the_artefact_and_never_touches_the_network(tmp_path,
                                                                          monkeypatch):
    from scripts import run_subscriptions

    monkeypatch.setattr(run_subscriptions, "SPOOL", tmp_path)
    status = run_subscriptions.deliver("spool", "", "report.csv", "id,x\n1,y\n")
    assert "spooled to" in status
    assert (tmp_path / "report.csv").read_text() == "id,x\n1,y\n"


def test_deliver_email_with_no_recipients_does_not_pretend(tmp_path, monkeypatch):
    from scripts import run_subscriptions

    monkeypatch.setattr(run_subscriptions, "SPOOL", tmp_path)
    status = run_subscriptions.deliver("email", "  ,  ", "report.csv", "x", tenant_id="t1")
    assert status.startswith("NOT SENT - no recipients named")


def test_deliver_email_with_no_channel_configured_reports_that_honestly(
        tid, tmp_path, monkeypatch, clean_channel):
    from scripts import run_subscriptions

    monkeypatch.setattr(run_subscriptions, "SPOOL", tmp_path)
    status = run_subscriptions.deliver(
        "email", "compliance@bank.example.com", "report.csv", "x", tenant_id=tid)
    assert status.startswith("NOT SENT to compliance@bank.example.com")
    assert "artefact retained" in status


def test_deliver_email_reaches_a_configured_recipient(tid, tmp_path, monkeypatch,
                                                       clean_channel, token_for,
                                                       notification_client):
    from scripts import run_subscriptions
    from services.notification_service.app import channels

    notification_client.put(
        f"/notifications/{tid}/channels", headers=token_for("tenant_admin"),
        json={"channel": "email", "enabled": True,
              "config": {"host": "relay.bank.example.com"}})
    monkeypatch.setattr(channels, "send_email",
                        lambda cfg, **kw: channels.Result(True, "sent"))
    monkeypatch.setattr(run_subscriptions, "SPOOL", tmp_path)

    status = run_subscriptions.deliver(
        "email", "compliance@bank.example.com", "report.csv", "id,x\n1,y\n",
        tenant_id=tid)
    assert status.startswith("sent to compliance@bank.example.com")


def test_deliver_email_records_a_partial_failure_across_recipients(
        tid, tmp_path, monkeypatch, clean_channel, token_for, notification_client):
    """One bad address must not swallow the fact that others got it."""
    from scripts import run_subscriptions
    from services.notification_service.app import channels

    notification_client.put(
        f"/notifications/{tid}/channels", headers=token_for("tenant_admin"),
        json={"channel": "email", "enabled": True,
              "config": {"host": "relay.bank.example.com"}})

    def flaky(cfg, *, to, subject, body, link="", attachment=None):
        return channels.Result(to == "good@bank.example.com", f"outcome for {to}")

    monkeypatch.setattr(channels, "send_email", flaky)
    monkeypatch.setattr(run_subscriptions, "SPOOL", tmp_path)

    status = run_subscriptions.deliver(
        "email", "good@bank.example.com, bad@bank.example.com", "report.csv", "x",
        tenant_id=tid)
    assert "sent to good@bank.example.com" in status
    assert "NOT SENT to bad@bank.example.com" in status


# ---------------------------------------------------------------- end to end
def test_run_due_updates_the_subscription_after_a_spool_delivery(
        analytics_client, token_for, tid, clean_subscriptions, tmp_path, monkeypatch):
    from scripts import run_subscriptions
    from services.tenant_service.app.models import Tenant

    monkeypatch.setattr(run_subscriptions, "SPOOL", tmp_path)
    made = analytics_client.post(
        f"/analytics/{tid}/subscriptions", headers=token_for("risk_manager"),
        json={"name": "e2e-spool", "entity": "case", "driver": "spool"})
    assert made.status_code == 201, made.text

    db = SessionLocal()
    try:
        tenant = db.get(Tenant, tid)
        slug = tenant.slug
    finally:
        db.close()

    ran = run_subscriptions.run_due(force=True, only_tenant=slug)
    assert ran >= 1

    listed = analytics_client.get(f"/analytics/{tid}/subscriptions",
                                  headers=token_for("risk_manager")).json()
    row = next(s for s in listed if s["name"] == "e2e-spool")
    assert row["run_count"] >= 1
    assert row["last_status"].startswith("spooled to")
    assert row["last_run_at"] is not None
    assert row["due_now"] is False, "a subscription just run on a weekly cadence " \
                                     "should not be due again immediately"


def test_run_due_delivers_by_email_when_a_channel_is_configured(
        analytics_client, token_for, notification_client, tid, clean_subscriptions,
        clean_channel, tmp_path, monkeypatch):
    from scripts import run_subscriptions
    from services.notification_service.app import channels
    from services.tenant_service.app.models import Tenant

    notification_client.put(
        f"/notifications/{tid}/channels", headers=token_for("tenant_admin"),
        json={"channel": "email", "enabled": True,
              "config": {"host": "relay.bank.example.com"}})
    monkeypatch.setattr(channels, "send_email",
                        lambda cfg, **kw: channels.Result(True, "sent"))
    monkeypatch.setattr(run_subscriptions, "SPOOL", tmp_path)

    made = analytics_client.post(
        f"/analytics/{tid}/subscriptions", headers=token_for("risk_manager"),
        json={"name": "e2e-email", "entity": "case", "driver": "email",
              "recipients": "compliance@bank.example.com"})
    assert made.status_code == 201, made.text

    db = SessionLocal()
    try:
        slug = db.get(Tenant, tid).slug
    finally:
        db.close()

    ran = run_subscriptions.run_due(force=True, only_tenant=slug)
    assert ran >= 1

    listed = analytics_client.get(f"/analytics/{tid}/subscriptions",
                                  headers=token_for("risk_manager")).json()
    row = next(s for s in listed if s["name"] == "e2e-email")
    assert row["last_status"].startswith("sent to compliance@bank.example.com")
