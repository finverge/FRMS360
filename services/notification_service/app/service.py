"""Raising, delivering and resolving notifications."""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from . import channels, rules
from .models import ChannelConfig, Delivery, Notification, Preference


def raise_notification(db: Session, *, tenant_id: str, recipient: str, kind: str,
                       dedupe_key: str, context: dict | None = None,
                       link: str = "") -> tuple[Notification | None, bool]:
    """Create a notification unless this exact condition is already outstanding.

    Returns (notification, created). ``created`` is False when the condition was already
    raised - which is the normal case on every watcher pass after the first, and the
    reason a compliance clock does not produce a reminder every five minutes.

    An already-read notification that has since been *resolved* is allowed to fire again:
    a filing that went overdue, was filed, and went overdue again on a reopened case is
    genuinely a new condition.
    """
    context = context or {}
    subject, body, severity = rules.render(kind, context)

    existing = db.scalar(select(Notification).where(
        Notification.tenant_id == tenant_id,
        Notification.recipient == recipient,
        Notification.dedupe_key == dedupe_key))
    if existing is not None:
        if existing.resolved_at is None:
            return existing, False
        # The condition returned. Reopen the same row rather than accumulating history
        # in the inbox.
        existing.resolved_at = None
        existing.read_at = None
        existing.created_at = datetime.now(timezone.utc)
        existing.subject, existing.body, existing.severity = subject, body, severity
        existing.context = context
        db.commit()
        _queue_deliveries(db, existing)
        return existing, True

    note = Notification(tenant_id=tenant_id, recipient=recipient, kind=kind,
                        dedupe_key=dedupe_key, severity=severity, subject=subject,
                        body=body, link=link, context=context)
    db.add(note)
    try:
        db.commit()
    except IntegrityError:
        # Two watchers raced on the same condition. The unique constraint is the
        # arbiter; both wanted the same outcome.
        db.rollback()
        return db.scalar(select(Notification).where(
            Notification.tenant_id == tenant_id,
            Notification.recipient == recipient,
            Notification.dedupe_key == dedupe_key)), False

    _queue_deliveries(db, note)
    return note, True


def _queue_deliveries(db: Session, note: Notification) -> None:
    """Decide which channels this notification should leave the platform on.

    The in-app inbox is not a channel: the notification row *is* the inbox entry, and it
    is never suppressed. Everything below is about pushing it somewhere else as well.
    """
    pref = db.scalar(select(Preference).where(
        Preference.tenant_id == note.tenant_id,
        Preference.recipient == note.recipient))
    if pref and rules.is_muted(note.kind, pref.muted_kinds):
        return

    configs = db.scalars(select(ChannelConfig).where(
        ChannelConfig.tenant_id == note.tenant_id,
        ChannelConfig.enabled.is_(True))).all()

    for cfg in configs:
        if not rules.at_least(note.severity, cfg.min_severity):
            continue
        if cfg.channel == "email":
            if pref and not pref.email_enabled and note.kind not in rules.UNMUTABLE:
                continue
            target = note.recipient
        else:
            target = (cfg.config or {}).get("url", "")
        db.add(Delivery(notification_id=note.id, tenant_id=note.tenant_id,
                        channel=cfg.channel, target=target,
                        next_attempt_at=datetime.now(timezone.utc)))
    db.commit()


def resolve(db: Session, *, tenant_id: str, dedupe_prefix: str) -> int:
    """Clear notifications whose condition no longer holds.

    Derived-from-state notifications should leave the inbox when the state changes -
    an overdue filing that has been filed is not something anyone should have to
    dismiss by hand.
    """
    rows = db.scalars(select(Notification).where(
        Notification.tenant_id == tenant_id,
        Notification.dedupe_key.like(dedupe_prefix + "%"),
        Notification.resolved_at.is_(None))).all()
    now = datetime.now(timezone.utc)
    for r in rows:
        r.resolved_at = now
    db.commit()
    return len(rows)


def run_deliveries(db: Session, *, limit: int = 200) -> dict:
    """Attempt every delivery that is due. Safe to run from several workers."""
    now = datetime.now(timezone.utc)
    due = db.scalars(select(Delivery).where(
        Delivery.status == "pending",
        Delivery.next_attempt_at <= now).limit(limit)).all()

    sent = failed = abandoned = 0
    for d in due:
        note = db.get(Notification, d.notification_id)
        if note is None:
            d.status = "abandoned"
            d.last_error = "notification no longer exists"
            abandoned += 1
            continue

        cfg = db.scalar(select(ChannelConfig).where(
            ChannelConfig.tenant_id == d.tenant_id,
            ChannelConfig.channel == d.channel))
        # Timestamps as ISO strings, not datetimes. httpx's json= uses the stdlib
        # encoder, which refuses a datetime - so a webhook carrying created_at failed
        # every single time, against a working endpoint as readily as a broken one.
        payload = {"id": note.id, "tenant_id": note.tenant_id,
                   "recipient": note.recipient, "kind": note.kind,
                   "severity": note.severity, "subject": note.subject,
                   "body": note.body, "link": note.link, "context": note.context,
                   "created_at": note.created_at.isoformat()
                   if note.created_at else None}

        # No configured channel means the message is spooled and labelled as not sent,
        # rather than marked delivered to nowhere.
        channel = d.channel if (cfg and cfg.enabled) else "spool"
        result = channels.deliver(channel, (cfg.config if cfg else {}) or {},
                                  notification=payload)

        d.attempts += 1
        if result.ok:
            d.status, d.sent_at, d.last_error = "sent", now, result.detail[:500]
            sent += 1
        else:
            d.last_error = result.detail[:500]
            nxt = channels.next_attempt(d.attempts, (cfg.config if cfg else {}) or {})
            if nxt is None:
                d.status = "abandoned"
                abandoned += 1
            else:
                d.next_attempt_at = nxt
                failed += 1
    db.commit()
    return {"attempted": len(due), "sent": sent, "retrying": failed,
            "abandoned": abandoned}


def inbox(db: Session, *, tenant_id: str, recipient: str, unread_only: bool = False,
          limit: int = 50) -> list[Notification]:
    q = select(Notification).where(
        Notification.tenant_id == tenant_id,
        Notification.recipient == recipient,
        Notification.resolved_at.is_(None))
    if unread_only:
        q = q.where(Notification.read_at.is_(None))
    return list(db.scalars(q.order_by(Notification.created_at.desc()).limit(limit)))
