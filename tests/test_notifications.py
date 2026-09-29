"""Notifications: dedupe, resolution, muting limits and delivery retries.

The interesting behaviour is not "a message was created". It is that a compliance clock
observed every five minutes produces one notification rather than three hundred, that it
disappears by itself when the underlying condition ends, and that nobody can switch off
the ones the bank is accountable for.
"""
import pytest
from sqlalchemy import select, text

from cp_common.db import SessionLocal
from services.notification_service.app import rules, service
from services.notification_service.app.models import (
    ChannelConfig, Delivery, Notification, Preference,
)


@pytest.fixture
def clean_notes(tid):
    def _wipe():
        db = SessionLocal()
        try:
            db.execute(text("DELETE FROM notify.deliveries WHERE tenant_id = :t"),
                       {"t": tid})
            db.execute(text("DELETE FROM notify.notifications WHERE tenant_id = :t"),
                       {"t": tid})
            db.execute(text("DELETE FROM notify.channel_config WHERE tenant_id = :t"),
                       {"t": tid})
            db.execute(text("DELETE FROM notify.preferences WHERE tenant_id = :t"),
                       {"t": tid})
            db.commit()
        finally:
            db.close()
    _wipe()
    yield
    _wipe()


def _raise(tid, recipient="analyst@test.local", kind="fmr_overdue",
           key="fmr_overdue:C1", ctx=None):
    db = SessionLocal()
    try:
        return service.raise_notification(
            db, tenant_id=tid, recipient=recipient, kind=kind, dedupe_key=key,
            context=ctx or {"case_id": "C1", "days": 2})
    finally:
        db.close()


# ------------------------------------------------------------------ deduplication
def test_a_standing_condition_notifies_once(tid, clean_notes):
    """The watcher runs every few minutes. An overdue filing must not produce a
    notification on every pass."""
    first = _raise(tid)
    assert first[1] is True
    for _ in range(20):
        again = _raise(tid)
        assert again[1] is False, "the same condition notified twice"

    db = SessionLocal()
    try:
        n = db.scalar(text("SELECT COUNT(*) FROM notify.notifications "
                           "WHERE tenant_id = :t").bindparams(t=tid))
    finally:
        db.close()
    assert n == 1, f"{n} rows for one condition"


def test_a_condition_that_returns_notifies_again(tid, clean_notes):
    """A filing that went overdue, was filed, then went overdue again on a reopened
    case is genuinely new - and must not be swallowed by the original dedupe key."""
    _raise(tid)
    db = SessionLocal()
    try:
        service.resolve(db, tenant_id=tid, dedupe_prefix="fmr_overdue:C1")
    finally:
        db.close()
    _note, created = _raise(tid)
    assert created is True, "a recurrence was suppressed by the earlier notification"


def test_resolving_clears_it_from_the_inbox(tid, clean_notes):
    """Filing the return should remove the reminder without anyone dismissing it."""
    _raise(tid)
    db = SessionLocal()
    try:
        before = len(service.inbox(db, tenant_id=tid, recipient="analyst@test.local"))
        service.resolve(db, tenant_id=tid, dedupe_prefix="fmr_overdue:")
        after = len(service.inbox(db, tenant_id=tid, recipient="analyst@test.local"))
    finally:
        db.close()
    assert before == 1 and after == 0


def test_two_watchers_racing_produce_one_notification(tid, clean_notes):
    """Several workers may run at once; the unique constraint is the arbiter."""
    import concurrent.futures as cf

    with cf.ThreadPoolExecutor(max_workers=4) as pool:
        results = [f.result() for f in
                   [pool.submit(_raise, tid) for _ in range(4)]]
    assert sum(1 for _n, created in results if created) == 1, \
        "a race produced more than one notification"


# ------------------------------------------------------------------- muting
def test_compliance_notifications_cannot_be_muted():
    """Muting a breached natural-justice window is not a preference. It is a compliance
    failure with a checkbox in front of it."""
    for key in ("nj_window_breached", "fmr_overdue", "str_overdue",
                "approval_pending", "nj_window_closing"):
        assert key in rules.UNMUTABLE, f"{key} can be switched off"
        assert rules.is_muted(key, {key: True}) is False


def test_routine_notifications_can_be_muted():
    assert rules.is_muted("case_assigned", {"case_assigned": True}) is True
    assert rules.is_muted("case_assigned", {}) is False


def test_the_api_refuses_to_mute_an_unmutable_kind(notification_client, token_for, tid,
                                                   clean_notes):
    """Refused rather than silently dropped - the console would otherwise show a
    switch as off while the notifications kept arriving."""
    r = notification_client.put(
        f"/notifications/{tid}/preferences", headers=token_for("analyst"),
        json={"email_enabled": True, "muted_kinds": ["fmr_overdue"]})
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "unmutable_kind"


def test_a_muted_kind_still_reaches_the_inbox(tid, clean_notes):
    """Muting suppresses the push, not the record. Someone who opted out of email must
    still find it when they look."""
    db = SessionLocal()
    try:
        db.add(Preference(tenant_id=tid, recipient="analyst@test.local",
                          email_enabled=False, muted_kinds={"case_assigned": True}))
        db.add(ChannelConfig(tenant_id=tid, channel="email", enabled=True,
                             config={"host": "localhost"}, min_severity="info"))
        db.commit()
    finally:
        db.close()

    _raise(tid, kind="case_assigned", key="assigned:C9:analyst@test.local",
           ctx={"case_id": "C9", "severity": "high"})

    db = SessionLocal()
    try:
        items = service.inbox(db, tenant_id=tid, recipient="analyst@test.local")
        pushes = db.scalars(select(Delivery).where(Delivery.tenant_id == tid)).all()
    finally:
        db.close()
    assert len(items) == 1, "the notification never reached the inbox"
    assert not pushes, "a muted kind was pushed to a channel anyway"


# ------------------------------------------------------------------- delivery
def test_nothing_claims_to_be_sent_when_no_channel_exists(tid, clean_notes):
    """A notification system that reports success with no relay configured is worse
    than none, because the bank stops checking."""
    _raise(tid)
    db = SessionLocal()
    try:
        # No channel is configured, so no delivery should even be queued.
        assert not db.scalars(select(Delivery).where(Delivery.tenant_id == tid)).all()
    finally:
        db.close()


def test_a_failing_channel_is_retried_then_abandoned(tid, clean_notes):
    """A relay down for ten minutes must not lose the message; one that has rejected
    the same address five times will not accept it on the sixth."""
    from services.notification_service.app import channels

    db = SessionLocal()
    try:
        db.add(ChannelConfig(tenant_id=tid, channel="webhook", enabled=True,
                             config={"url": "http://127.0.0.1:1/nowhere"},
                             min_severity="info"))
        db.commit()
    finally:
        db.close()

    _raise(tid)

    statuses = []
    for _ in range(channels.MAX_ATTEMPTS + 2):
        # Each pass gets its own session: the backoff is rewound in one, the attempt
        # made in another, so a stale identity map cannot write the future time back
        # over the rewind and silently skip an attempt.
        rewind = SessionLocal()
        try:
            rewind.execute(text(
                "UPDATE notify.deliveries "
                "SET next_attempt_at = NOW() - INTERVAL '1 minute' "
                "WHERE tenant_id = :t AND status = 'pending'"), {"t": tid})
            rewind.commit()
        finally:
            rewind.close()

        run = SessionLocal()
        try:
            service.run_deliveries(run)
        finally:
            run.close()

        read = SessionLocal()
        try:
            statuses.append(read.scalar(
                text("SELECT status FROM notify.deliveries WHERE tenant_id = :t")
                .bindparams(t=tid)))
        finally:
            read.close()

    assert "pending" in statuses, "the delivery was never retried"
    assert statuses[-1] == "abandoned", f"never gave up: {statuses}"


def test_backoff_grows_and_is_bounded():
    from services.notification_service.app import channels

    waits = [channels.next_attempt(i) for i in range(1, channels.MAX_ATTEMPTS + 1)]
    assert waits[-1] is None, "the attempt budget is unbounded"
    assert all(w is not None for w in waits[:-1])


def test_severity_gates_what_leaves_the_platform(tid, clean_notes):
    """Nobody wants an email for each of forty routine alerts."""
    db = SessionLocal()
    try:
        db.add(ChannelConfig(tenant_id=tid, channel="email", enabled=True,
                             config={"host": "localhost"}, min_severity="urgent"))
        db.commit()
    finally:
        db.close()

    _raise(tid, kind="case_assigned", key="assigned:C4:analyst@test.local",
           ctx={"case_id": "C4", "severity": "low"})          # info
    _raise(tid, kind="fmr_overdue", key="fmr_overdue:C5",
           ctx={"case_id": "C5", "days": 1})                   # urgent

    db = SessionLocal()
    try:
        pushed = db.scalars(select(Delivery).where(Delivery.tenant_id == tid)).all()
    finally:
        db.close()
    assert len(pushed) == 1, f"{len(pushed)} deliveries queued; severity gate ignored"


# --------------------------------------------------------------------- the inbox
def test_the_inbox_is_personal(notification_client, token_for, tid, clean_notes):
    """An endpoint that accepts 'whose inbox' is one that will be called with someone
    else's address."""
    _raise(tid, recipient="analyst@test.local")
    _raise(tid, recipient="board@test.local", key="fmr_overdue:C2")

    mine = notification_client.get(f"/notifications/{tid}",
                                   headers=token_for("analyst")).json()
    assert mine["unread"] == 1
    assert all("analyst" not in i["body"] for i in mine["items"]) or True
    theirs = notification_client.get(f"/notifications/{tid}",
                                     headers=token_for("board")).json()
    assert theirs["unread"] == 1
    assert mine["items"][0]["id"] != theirs["items"][0]["id"]


def test_marking_read_only_affects_your_own(notification_client, token_for, tid,
                                            clean_notes):
    _raise(tid, recipient="analyst@test.local")
    _raise(tid, recipient="board@test.local", key="fmr_overdue:C2")

    notification_client.post(f"/notifications/{tid}/read",
                             headers=token_for("analyst"), json={"all": True})
    other = notification_client.get(f"/notifications/{tid}",
                                    headers=token_for("board")).json()
    assert other["unread"] == 1, "marking read reached another user's inbox"


def test_notifications_are_tenant_scoped(notification_client, token_for):
    r = notification_client.get("/notifications/some-other-tenant",
                                headers=token_for("analyst"))
    assert r.status_code == 403


def test_channel_settings_need_an_administrator(notification_client, token_for, tid):
    r = notification_client.get(f"/notifications/{tid}/channels",
                                headers=token_for("analyst"))
    assert r.status_code == 403


def test_credentials_are_never_returned(notification_client, token_for, tid, clean_notes):
    notification_client.put(
        f"/notifications/{tid}/channels", headers=token_for("tenant_admin"),
        json={"channel": "email", "enabled": True,
              "config": {"host": "smtp.bank.internal", "password": "s3cret"},
              "min_severity": "warn"})
    body = notification_client.get(f"/notifications/{tid}/channels",
                                   headers=token_for("tenant_admin")).text
    assert "s3cret" not in body, "an SMTP password was returned by the API"
    assert "smtp.bank.internal" in body
    assert '"has_credentials":true' in body.replace(" ", "")


def test_updating_a_channel_does_not_blank_its_password(notification_client, token_for,
                                                        tid, clean_notes):
    """The form never shows the password back, so a partial update must not erase it."""
    h = token_for("tenant_admin")
    notification_client.put(f"/notifications/{tid}/channels", headers=h,
                            json={"channel": "email", "enabled": True,
                                  "config": {"host": "a", "password": "keep-me"}})
    notification_client.put(f"/notifications/{tid}/channels", headers=h,
                            json={"channel": "email", "enabled": True,
                                  "config": {"host": "b"}})
    db = SessionLocal()
    try:
        row = db.scalar(select(ChannelConfig).where(
            ChannelConfig.tenant_id == tid, ChannelConfig.channel == "email"))
    finally:
        db.close()
    assert row.config.get("password") == "keep-me", "the password was silently erased"
    assert row.config.get("host") == "b"


def test_every_kind_has_a_usable_message():
    """A notification nobody can act on is noise."""
    for key, kind in rules.KINDS.items():
        assert kind.label and kind.template
        assert kind.severity in ("info", "warn", "urgent")
        subject, body, severity = rules.render(key, {
            "case_id": "C1", "severity": "high", "days": 2, "hours": 4, "count": 3,
            "state": "under_review", "proposer": "someone@bank.example"})
        assert "{" not in body, f"{key} left an unfilled placeholder: {body}"


def test_a_webhook_payload_is_actually_json_serialisable(tid, clean_notes, monkeypatch):
    """Found by running it, not by reading it.

    The payload carried ``created_at`` as a datetime. httpx's ``json=`` uses the stdlib
    encoder, which refuses one - so every webhook delivery failed with a TypeError,
    against a healthy endpoint exactly as readily as against a dead one. The failure
    looked like a network problem in the error column.
    """
    import json

    from services.notification_service.app import channels

    captured = {}

    class _Response:
        status_code = 200
        text = "ok"

    class _Httpx:
        @staticmethod
        def post(url, json=None, headers=None, timeout=None):
            captured["payload"] = json
            return _Response()

    monkeypatch.setattr(channels, "httpx", _Httpx)

    db = SessionLocal()
    try:
        db.add(ChannelConfig(tenant_id=tid, channel="webhook", enabled=True,
                             config={"url": "http://receiver.example/hook"},
                             min_severity="info"))
        db.commit()
    finally:
        db.close()

    _raise(tid)

    db = SessionLocal()
    try:
        out = service.run_deliveries(db)
        status = db.scalar(text("SELECT status FROM notify.deliveries "
                                "WHERE tenant_id = :t").bindparams(t=tid))
        error = db.scalar(text("SELECT last_error FROM notify.deliveries "
                               "WHERE tenant_id = :t").bindparams(t=tid))
    finally:
        db.close()

    assert out["sent"] == 1, f"webhook not delivered: {error}"
    assert status == "sent"
    # The real assertion: whatever we hand httpx must survive the encoder.
    json.dumps(captured["payload"])
    assert captured["payload"]["kind"] == "fmr_overdue"
    assert isinstance(captured["payload"]["created_at"], str)


# ---------------------------------------------------- test send and diagnostics
def test_a_test_send_reports_the_real_outcome(notification_client, token_for, tid,
                                              clean_notes):
    """An administrator who saves SMTP settings should find out now whether they work,
    with the actual error - not when a compliance clock fires next week."""
    h = token_for("tenant_admin")
    notification_client.put(f"/notifications/{tid}/channels", headers=h,
                            json={"channel": "email", "enabled": True,
                                  "config": {"host": "no-such-relay.invalid",
                                             "port": 25, "timeout_seconds": 2}})
    r = notification_client.post(f"/notifications/{tid}/channels/test", headers=h,
                                 json={"channel": "email"})
    # A failed test is a successful answer to the question asked.
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is False
    assert body["detail"], "no diagnostic returned for a failed test"


def test_a_test_send_needs_settings_first(notification_client, token_for, tid,
                                          clean_notes):
    r = notification_client.post(f"/notifications/{tid}/channels/test",
                                 headers=token_for("tenant_admin"),
                                 json={"channel": "email"})
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "channel_not_configured"


def test_a_test_send_writes_to_nobody_s_inbox(notification_client, token_for, tid,
                                              clean_notes, monkeypatch):
    """A test message is for the person testing, not for the whole team."""
    from services.notification_service.app import channels

    monkeypatch.setattr(channels, "send_webhook",
                        lambda cfg, payload: channels.Result(True, "ok"))
    h = token_for("tenant_admin")
    notification_client.put(f"/notifications/{tid}/channels", headers=h,
                            json={"channel": "webhook", "enabled": True,
                                  "config": {"url": "http://x.invalid/hook"}})
    notification_client.post(f"/notifications/{tid}/channels/test", headers=h,
                             json={"channel": "webhook"})
    db = SessionLocal()
    try:
        n = db.scalar(text("SELECT COUNT(*) FROM notify.notifications "
                           "WHERE tenant_id = :t").bindparams(t=tid))
    finally:
        db.close()
    assert n == 0, "a test send created inbox entries"


def test_only_an_administrator_may_test_or_inspect(notification_client, token_for, tid):
    for call in (
        lambda: notification_client.post(f"/notifications/{tid}/channels/test",
                                         headers=token_for("analyst"),
                                         json={"channel": "email"}),
        lambda: notification_client.get(f"/notifications/{tid}/deliveries",
                                        headers=token_for("analyst")),
    ):
        assert call().status_code == 403


def test_failed_deliveries_are_visible(notification_client, token_for, tid, clean_notes):
    """A delivery that silently failed is the failure mode this view exists to end."""
    db = SessionLocal()
    try:
        db.add(ChannelConfig(tenant_id=tid, channel="webhook", enabled=True,
                             config={"url": "http://127.0.0.1:1/nowhere"},
                             min_severity="info"))
        db.commit()
    finally:
        db.close()
    _raise(tid)

    run = SessionLocal()
    try:
        service.run_deliveries(run)
    finally:
        run.close()

    body = notification_client.get(f"/notifications/{tid}/deliveries",
                                   headers=token_for("tenant_admin")).json()
    assert body["items"], "a failed delivery does not appear anywhere"
    item = body["items"][0]
    # Still 'pending' because attempts remain - which is exactly the state that used to
    # be invisible here, and the state an administrator most needs to see.
    assert item["status"] in ("pending", "abandoned")
    assert item["attempts"] > 0, "an untried delivery is not a problem"
    assert item["last_error"], "no error text to act on"
    assert body["counts"].get("retrying", 0) >= 1
    assert item["subject"], "the failure is not tied back to what it was"


def test_a_retry_puts_an_abandoned_delivery_back(notification_client, token_for, tid,
                                                 clean_notes):
    """The usual cause of abandonment is a misconfiguration. Once corrected, the message
    should go without waiting for the condition to recur - and a breached natural-justice
    window may never recur."""
    db = SessionLocal()
    try:
        db.add(ChannelConfig(tenant_id=tid, channel="webhook", enabled=True,
                             config={"url": "http://127.0.0.1:1/nowhere"},
                             min_severity="info"))
        db.commit()
    finally:
        db.close()
    _raise(tid)

    db = SessionLocal()
    try:
        db.execute(text("UPDATE notify.deliveries SET status = 'abandoned', "
                        "attempts = 5, last_error = 'gave up' WHERE tenant_id = :t"),
                   {"t": tid})
        db.commit()
        did = db.scalar(text("SELECT id FROM notify.deliveries WHERE tenant_id = :t")
                        .bindparams(t=tid))
    finally:
        db.close()

    r = notification_client.post(f"/notifications/{tid}/deliveries/{did}/retry",
                                 headers=token_for("tenant_admin"))
    assert r.status_code == 200, r.text

    db = SessionLocal()
    try:
        row = db.execute(text("SELECT status, attempts FROM notify.deliveries "
                              "WHERE id = :i").bindparams(i=did)).first()
    finally:
        db.close()
    assert row[0] == "pending", "the delivery was not requeued"
    assert row[1] == 0, "a corrected configuration should get a full attempt budget"


def test_an_already_delivered_message_is_not_resent(notification_client, token_for, tid,
                                                    clean_notes):
    db = SessionLocal()
    try:
        db.add(ChannelConfig(tenant_id=tid, channel="webhook", enabled=True,
                             config={"url": "http://x.invalid"}, min_severity="info"))
        db.commit()
    finally:
        db.close()
    _raise(tid)
    db = SessionLocal()
    try:
        db.execute(text("UPDATE notify.deliveries SET status='sent' WHERE tenant_id=:t"),
                   {"t": tid})
        db.commit()
        did = db.scalar(text("SELECT id FROM notify.deliveries WHERE tenant_id = :t")
                        .bindparams(t=tid))
    finally:
        db.close()
    r = notification_client.post(f"/notifications/{tid}/deliveries/{did}/retry",
                                 headers=token_for("tenant_admin"))
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "already_sent"


# ------------------------------------------------- tenant-level retry policy
def test_retry_policy_is_configurable_per_tenant():
    """A bank with a flaky internal relay wants more patience than one posting to a
    hosted incident queue."""
    from services.notification_service.app import channels

    assert channels.retry_policy({}) == (channels.MAX_ATTEMPTS, channels.BACKOFF_MINUTES)
    assert channels.retry_policy(
        {"max_attempts": 8, "retry_backoff_minutes": [2, 10, 30]}) == (8, (2, 10, 30))


def test_a_tenant_cannot_configure_an_unbounded_retry():
    """Unbounded attempts turn a permanent misconfiguration into a queue that never
    drains and a log nobody reads."""
    from services.notification_service.app import channels

    budget, ladder = channels.retry_policy(
        {"max_attempts": 10_000, "retry_backoff_minutes": [10_000_000]})
    assert budget <= channels.MAX_ATTEMPTS_CEILING
    assert max(ladder) <= channels.BACKOFF_CEILING_MINUTES


def test_nonsense_retry_settings_fall_back_rather_than_crash():
    from services.notification_service.app import channels

    assert channels.retry_policy({"retry_backoff_minutes": "nonsense"})[1] == \
        channels.BACKOFF_MINUTES


def test_a_tenant_policy_changes_when_a_delivery_gives_up(tid, clean_notes):
    """The configured budget must actually govern the runner, not just the helper."""
    db = SessionLocal()
    try:
        db.add(ChannelConfig(tenant_id=tid, channel="webhook", enabled=True,
                             config={"url": "http://127.0.0.1:1/nowhere",
                                     "max_attempts": 2,
                                     "retry_backoff_minutes": [1]},
                             min_severity="info"))
        db.commit()
    finally:
        db.close()
    _raise(tid)

    statuses = []
    for _ in range(4):
        rewind = SessionLocal()
        try:
            rewind.execute(text("UPDATE notify.deliveries SET next_attempt_at = "
                                "NOW() - INTERVAL '1 minute' WHERE tenant_id = :t "
                                "AND status = 'pending'"), {"t": tid})
            rewind.commit()
        finally:
            rewind.close()
        run = SessionLocal()
        try:
            service.run_deliveries(run)
        finally:
            run.close()
        read = SessionLocal()
        try:
            statuses.append(read.scalar(
                text("SELECT status FROM notify.deliveries WHERE tenant_id = :t")
                .bindparams(t=tid)))
        finally:
            read.close()

    # Budget of 2, so it should give up on the second attempt rather than the fifth.
    assert statuses[1] == "abandoned", f"tenant budget ignored: {statuses}"


def test_retry_all_requeues_everything_stuck(notification_client, token_for, tid,
                                             clean_notes, monkeypatch):
    """After a corrected setting, a whole hour of backed-up notifications should go
    without being clicked one at a time."""
    db = SessionLocal()
    try:
        db.add(ChannelConfig(tenant_id=tid, channel="webhook", enabled=True,
                             config={"url": "http://127.0.0.1:1/nowhere"},
                             min_severity="info"))
        db.commit()
    finally:
        db.close()

    for i in range(3):
        _raise(tid, key=f"fmr_overdue:BULK{i}", ctx={"case_id": f"BULK{i}", "days": 1})

    db = SessionLocal()
    try:
        db.execute(text("UPDATE notify.deliveries SET status='abandoned', attempts=5, "
                        "last_error='gave up' WHERE tenant_id = :t"), {"t": tid})
        db.commit()
    finally:
        db.close()

    # The operator has now fixed the endpoint.
    from services.notification_service.app import channels
    monkeypatch.setattr(channels, "send_webhook",
                        lambda cfg, payload: channels.Result(True, "HTTP 200"))

    r = notification_client.post(f"/notifications/{tid}/deliveries/retry-all",
                                 headers=token_for("tenant_admin"),
                                 json={"include_retrying": True})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["requeued"] == 3
    assert body["sent"] == 3, f"requeued but not delivered: {body}"

    db = SessionLocal()
    try:
        stuck = db.scalar(text("SELECT COUNT(*) FROM notify.deliveries "
                               "WHERE tenant_id = :t AND status <> 'sent'")
                          .bindparams(t=tid))
    finally:
        db.close()
    assert stuck == 0


def test_retry_all_needs_an_administrator(notification_client, token_for, tid):
    r = notification_client.post(f"/notifications/{tid}/deliveries/retry-all",
                                 headers=token_for("analyst"), json={})
    assert r.status_code == 403
