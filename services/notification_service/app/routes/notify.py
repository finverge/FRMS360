"""Notification endpoints: an inbox for people, an internal door for services."""
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from cp_common import (
    AppError, Principal, get_current_principal, get_session, record_audit,
    require_internal_key, resolve_tenant_scope,
)

from .. import rules, service
from ..models import ChannelConfig, Delivery, Notification, Preference

router = APIRouter(tags=["notifications"])


# ------------------------------------------------------------------ the inbox
@router.get("/notifications/{tenant_id}")
def list_inbox(
    tenant_id: str,
    unread_only: bool = Query(default=False),
    limit: int = Query(default=50, ge=1, le=200),
    db: Session = Depends(get_session),
    principal: Principal = Depends(get_current_principal),
) -> dict:
    """This user's own notifications. Never anyone else's.

    Scoped to the caller rather than taking a recipient parameter: an inbox is personal,
    and an endpoint that accepts "whose" is an endpoint that will eventually be called
    with somebody else's address.
    """
    resolve_tenant_scope(principal, tenant_id)
    rows = service.inbox(db, tenant_id=tenant_id, recipient=principal.subject,
                         unread_only=unread_only, limit=limit)
    unread = len([r for r in service.inbox(db, tenant_id=tenant_id,
                                           recipient=principal.subject,
                                           unread_only=True, limit=200)])
    return {"unread": unread, "items": [
        {"id": r.id, "kind": r.kind, "label": rules.KINDS[r.kind].label
         if r.kind in rules.KINDS else r.kind,
         "severity": r.severity, "subject": r.subject, "body": r.body, "link": r.link,
         "created_at": r.created_at, "read": r.read_at is not None,
         "context": r.context}
        for r in rows]}


class MarkRead(BaseModel):
    ids: list[str] = Field(default_factory=list)
    all: bool = False


@router.post("/notifications/{tenant_id}/read")
def mark_read(
    tenant_id: str,
    payload: MarkRead,
    db: Session = Depends(get_session),
    principal: Principal = Depends(get_current_principal),
) -> dict:
    resolve_tenant_scope(principal, tenant_id)
    q = select(Notification).where(
        Notification.tenant_id == tenant_id,
        Notification.recipient == principal.subject,
        Notification.read_at.is_(None))
    if not payload.all:
        if not payload.ids:
            return {"marked": 0}
        q = q.where(Notification.id.in_(payload.ids))
    rows = db.scalars(q).all()
    now = datetime.now(timezone.utc)
    for r in rows:
        r.read_at = now
    db.commit()
    return {"marked": len(rows)}


# ------------------------------------------------------------- preferences
class PreferenceIn(BaseModel):
    email_enabled: bool = True
    muted_kinds: list[str] = Field(default_factory=list)


@router.get("/notifications/{tenant_id}/preferences")
def get_preferences(
    tenant_id: str,
    db: Session = Depends(get_session),
    principal: Principal = Depends(get_current_principal),
) -> dict:
    resolve_tenant_scope(principal, tenant_id)
    pref = db.scalar(select(Preference).where(
        Preference.tenant_id == tenant_id, Preference.recipient == principal.subject))
    muted = list((pref.muted_kinds or {}) if pref else [])
    return {
        "email_enabled": pref.email_enabled if pref else True,
        "muted_kinds": muted,
        # Sent so the console can render the unmutable ones as fixed rather than
        # letting someone toggle a switch that will be ignored.
        "kinds": [{"key": k.key, "label": k.label, "severity": k.severity,
                   "mutable": k.mutable} for k in rules.KINDS.values()],
    }


@router.put("/notifications/{tenant_id}/preferences")
def set_preferences(
    tenant_id: str,
    payload: PreferenceIn,
    db: Session = Depends(get_session),
    principal: Principal = Depends(get_current_principal),
) -> dict:
    resolve_tenant_scope(principal, tenant_id)
    # Silently dropping an unmutable kind would let the console show it as muted.
    refused = [k for k in payload.muted_kinds if k in rules.UNMUTABLE]
    if refused:
        raise AppError(
            "These notifications cannot be switched off because the bank is "
            "accountable for acting on them: " + ", ".join(
                rules.KINDS[k].label for k in refused),
            400, "unmutable_kind")

    pref = db.scalar(select(Preference).where(
        Preference.tenant_id == tenant_id, Preference.recipient == principal.subject))
    if pref is None:
        pref = Preference(tenant_id=tenant_id, recipient=principal.subject)
        db.add(pref)
    pref.email_enabled = payload.email_enabled
    pref.muted_kinds = {k: True for k in payload.muted_kinds}
    pref.updated_at = datetime.now(timezone.utc)
    db.commit()
    return {"email_enabled": pref.email_enabled,
            "muted_kinds": list(pref.muted_kinds or {})}


# --------------------------------------------------------- channel configuration
class ChannelIn(BaseModel):
    channel: str
    enabled: bool = True
    config: dict = Field(default_factory=dict)
    min_severity: str = "warn"


@router.get("/notifications/{tenant_id}/channels")
def list_channels(
    tenant_id: str,
    db: Session = Depends(get_session),
    principal: Principal = Depends(get_current_principal),
) -> list[dict]:
    from cp_common.dynamic_roles import get_role

    resolve_tenant_scope(principal, tenant_id)
    if not get_role(tenant_id, principal.role).can_admin_tenant:
        raise AppError("Only a tenant administrator may view delivery settings", 403,
                       "role_not_permitted")
    rows = db.scalars(select(ChannelConfig).where(
        ChannelConfig.tenant_id == tenant_id)).all()
    # Credentials are never returned - not even to the administrator who set them.
    return [{"channel": r.channel, "enabled": r.enabled,
             "min_severity": r.min_severity,
             "config": {k: v for k, v in (r.config or {}).items()
                        if k not in ("password", "headers")},
             "has_credentials": bool((r.config or {}).get("password") or
                                     (r.config or {}).get("headers"))}
            for r in rows]


@router.put("/notifications/{tenant_id}/channels")
def set_channel(
    tenant_id: str,
    payload: ChannelIn,
    db: Session = Depends(get_session),
    principal: Principal = Depends(get_current_principal),
) -> dict:
    from cp_common.dynamic_roles import get_role

    resolve_tenant_scope(principal, tenant_id)
    if not get_role(tenant_id, principal.role).can_admin_tenant:
        raise AppError("Only a tenant administrator may change delivery settings", 403,
                       "role_not_permitted")
    if payload.channel not in ("email", "webhook"):
        raise AppError("Unsupported channel", 400, "unknown_channel")

    row = db.scalar(select(ChannelConfig).where(
        ChannelConfig.tenant_id == tenant_id, ChannelConfig.channel == payload.channel))
    if row is None:
        row = ChannelConfig(tenant_id=tenant_id, channel=payload.channel)
        db.add(row)
    row.enabled = payload.enabled
    row.min_severity = payload.min_severity
    # Merge, so updating the host does not silently blank a password the form never
    # showed back to the administrator.
    merged = dict(row.config or {})
    merged.update(payload.config or {})
    row.config = merged
    db.commit()
    record_audit(
        service="notification-service", action="notify.channel_configured",
        actor=principal.subject, actor_role=principal.role, tenant_id=tenant_id,
        target_type="channel", target_id=payload.channel, status="success",
        detail={"enabled": row.enabled, "min_severity": row.min_severity})
    return {"channel": row.channel, "enabled": row.enabled}


class TestSendIn(BaseModel):
    channel: str
    #: Where to send it. Defaults to the administrator asking, because the useful test
    #: is "does it reach a person", not "did the socket open".
    to: str = ""


@router.post("/notifications/{tenant_id}/channels/test")
def send_test(
    tenant_id: str,
    payload: TestSendIn,
    db: Session = Depends(get_session),
    principal: Principal = Depends(get_current_principal),
) -> dict:
    """Send a test through a configured channel and report what actually happened.

    Synchronous and unqueued on purpose. The point of a test is an answer now, with the
    real error text if there is one - an administrator who saves SMTP settings should not
    have to wait for a compliance clock to fire to discover the relay name is wrong.

    It writes nothing to anyone's inbox: a test message is for the person testing.
    """
    from cp_common.dynamic_roles import get_role

    from .. import channels

    resolve_tenant_scope(principal, tenant_id)
    if not get_role(tenant_id, principal.role).can_admin_tenant:
        raise AppError("Only a tenant administrator may send a test", 403,
                       "role_not_permitted")

    cfg = db.scalar(select(ChannelConfig).where(
        ChannelConfig.tenant_id == tenant_id,
        ChannelConfig.channel == payload.channel))
    if cfg is None:
        raise AppError("Save the settings for this channel first", 400,
                       "channel_not_configured")

    to = (payload.to or principal.subject).strip()
    sample = {
        "id": "test", "tenant_id": tenant_id, "recipient": to, "kind": "test",
        "severity": "info",
        "subject": "FRMS test notification",
        "body": ("This is a test from the fraud platform, sent by "
                 f"{principal.subject}. If you are reading it, delivery works. "
                 "No action is needed."),
        "link": "", "context": {"test": True}, "created_at": None,
    }
    result = channels.deliver(payload.channel, cfg.config or {}, notification=sample)

    record_audit(
        service="notification-service", action="notify.test_send",
        actor=principal.subject, actor_role=principal.role, tenant_id=tenant_id,
        target_type="channel", target_id=payload.channel,
        status="success" if result.ok else "failure",
        detail={"to": to, "detail": result.detail[:300]})

    # A failed test is a successful answer to the question that was asked, so it is a
    # 200 carrying the outcome rather than an error the console has to unwrap.
    return {"ok": result.ok, "channel": payload.channel, "to": to,
            "detail": result.detail}


@router.get("/notifications/{tenant_id}/deliveries")
def list_deliveries(
    tenant_id: str,
    status: str = Query(default="problems"),
    limit: int = Query(default=50, ge=1, le=200),
    db: Session = Depends(get_session),
    principal: Principal = Depends(get_current_principal),
) -> dict:
    """Delivery attempts, so a silent failure is visible somewhere.

    ``status=problems`` is the default: what an administrator wants is the list of
    things that did not arrive, not a log of everything that did.
    """
    from cp_common.dynamic_roles import get_role

    resolve_tenant_scope(principal, tenant_id)
    if not get_role(tenant_id, principal.role).can_admin_tenant:
        raise AppError("Only a tenant administrator may view delivery history", 403,
                       "role_not_permitted")

    q = select(Delivery).where(Delivery.tenant_id == tenant_id)
    if status == "problems":
        # "Problem" means it has failed at least once and has still not arrived - which
        # includes deliveries that are mid-retry. A view showing only abandoned ones goes
        # on being empty for the whole hour a wrong relay name is backing messages up,
        # which is precisely the hour an administrator needs to see it.
        q = q.where((Delivery.status == "abandoned") |
                    ((Delivery.status == "pending") & (Delivery.attempts > 0)))
    elif status != "all":
        q = q.where(Delivery.status == status)

    rows = list(db.scalars(q.order_by(Delivery.created_at.desc()).limit(limit)))
    notes = {n.id: n for n in db.scalars(select(Notification).where(
        Notification.id.in_([r.notification_id for r in rows])))} if rows else {}

    counts = dict(db.execute(
        select(Delivery.status, func.count()).where(
            Delivery.tenant_id == tenant_id).group_by(Delivery.status)).all())

    # Reported separately so the console can say "3 retrying, 1 given up" rather than
    # making an administrator infer it from a list.
    retrying = db.scalar(select(func.count()).select_from(Delivery).where(
        Delivery.tenant_id == tenant_id, Delivery.status == "pending",
        Delivery.attempts > 0)) or 0
    counts["retrying"] = retrying

    return {"counts": counts, "items": [
        {"id": r.id, "channel": r.channel, "target": r.target, "status": r.status,
         "attempts": r.attempts, "last_error": r.last_error,
         "created_at": r.created_at, "sent_at": r.sent_at,
         "next_attempt_at": r.next_attempt_at,
         "subject": notes[r.notification_id].subject
         if r.notification_id in notes else "(notification removed)",
         "recipient": notes[r.notification_id].recipient
         if r.notification_id in notes else ""}
        for r in rows]}


class RetryAllIn(BaseModel):
    #: Limit to one channel, for when only the relay was wrong.
    channel: str = ""
    #: By default only the ones that gave up. Including mid-retry deliveries lets an
    #: administrator who has just corrected a setting stop waiting for the backoff.
    include_retrying: bool = True


@router.post("/notifications/{tenant_id}/deliveries/retry-all")
def retry_all(
    tenant_id: str,
    payload: RetryAllIn,
    db: Session = Depends(get_session),
    principal: Principal = Depends(get_current_principal),
) -> dict:
    """Requeue every stuck delivery for this tenant.

    The realistic sequence is: a relay name is wrong, an hour of compliance notifications
    quietly backs up and gives up, someone fixes the setting. Retrying them one at a time
    is not a recovery procedure, and several of those conditions - a breached response
    window, say - will never recur to re-raise themselves.
    """
    from cp_common.dynamic_roles import get_role

    resolve_tenant_scope(principal, tenant_id)
    if not get_role(tenant_id, principal.role).can_admin_tenant:
        raise AppError("Only a tenant administrator may retry deliveries", 403,
                       "role_not_permitted")

    states = ["abandoned"]
    q = select(Delivery).where(Delivery.tenant_id == tenant_id)
    if payload.include_retrying:
        q = q.where((Delivery.status == "abandoned") |
                    ((Delivery.status == "pending") & (Delivery.attempts > 0)))
    else:
        q = q.where(Delivery.status == "abandoned")
    if payload.channel:
        q = q.where(Delivery.channel == payload.channel)

    rows = list(db.scalars(q))
    now = datetime.now(timezone.utc)
    for r in rows:
        r.status = "pending"
        r.attempts = 0        # a corrected configuration deserves a full budget again
        r.next_attempt_at = now
        r.last_error = ""
    db.commit()

    record_audit(
        service="notification-service", action="notify.retry_all",
        actor=principal.subject, actor_role=principal.role, tenant_id=tenant_id,
        target_type="delivery", target_id=payload.channel or "all", status="success",
        detail={"requeued": len(rows), "include_retrying": payload.include_retrying})

    # Attempt them now rather than leaving the administrator to wonder whether the fix
    # worked until the next worker pass.
    outcome = service.run_deliveries(db)
    return {"requeued": len(rows), **outcome}


@router.post("/notifications/{tenant_id}/deliveries/{delivery_id}/retry")
def retry_delivery(
    tenant_id: str,
    delivery_id: str,
    db: Session = Depends(get_session),
    principal: Principal = Depends(get_current_principal),
) -> dict:
    """Put an abandoned delivery back in the queue.

    Exists because the usual cause of abandonment is a configuration mistake. Once it is
    corrected, the message should go without waiting for the condition to recur - and for
    a breached natural-justice window, it may not recur at all.
    """
    from cp_common.dynamic_roles import get_role

    resolve_tenant_scope(principal, tenant_id)
    if not get_role(tenant_id, principal.role).can_admin_tenant:
        raise AppError("Only a tenant administrator may retry a delivery", 403,
                       "role_not_permitted")

    row = db.get(Delivery, delivery_id)
    if row is None or row.tenant_id != tenant_id:
        raise AppError("Delivery not found", 404, "not_found")
    if row.status == "sent":
        raise AppError("That notification was already delivered", 400, "already_sent")

    row.status = "pending"
    row.attempts = 0          # a corrected configuration deserves a full budget again
    row.next_attempt_at = datetime.now(timezone.utc)
    row.last_error = ""
    db.commit()
    record_audit(
        service="notification-service", action="notify.delivery_retried",
        actor=principal.subject, actor_role=principal.role, tenant_id=tenant_id,
        target_type="delivery", target_id=delivery_id, status="success")
    return {"queued": True}


# ------------------------------------------------------------ internal (services)
class RaiseIn(BaseModel):
    tenant_id: str
    recipient: str
    kind: str
    dedupe_key: str
    context: dict = Field(default_factory=dict)
    link: str = ""


@router.post("/internal/notifications/raise",
             dependencies=[Depends(require_internal_key)])
def raise_one(payload: RaiseIn, db: Session = Depends(get_session)) -> dict:
    if payload.kind not in rules.KINDS:
        raise AppError(f"Unknown notification kind '{payload.kind}'", 400,
                       "unknown_kind")
    note, created = service.raise_notification(
        db, tenant_id=payload.tenant_id, recipient=payload.recipient,
        kind=payload.kind, dedupe_key=payload.dedupe_key, context=payload.context,
        link=payload.link)
    return {"id": note.id if note else None, "created": created}


class ResolveIn(BaseModel):
    tenant_id: str
    dedupe_prefix: str


@router.post("/internal/notifications/resolve",
             dependencies=[Depends(require_internal_key)])
def resolve_condition(payload: ResolveIn, db: Session = Depends(get_session)) -> dict:
    return {"resolved": service.resolve(db, tenant_id=payload.tenant_id,
                                        dedupe_prefix=payload.dedupe_prefix)}


@router.post("/internal/notifications/deliver",
             dependencies=[Depends(require_internal_key)])
def deliver_due(limit: int = Query(default=200, ge=1, le=1000),
                db: Session = Depends(get_session)) -> dict:
    return service.run_deliveries(db, limit=limit)


class SendDirectIn(BaseModel):
    tenant_id: str
    channel: str = "email"
    to: str
    subject: str
    body: str
    attachment_filename: str = ""
    attachment_content_b64: str = ""
    attachment_mime: str = "text/csv"


@router.post("/internal/channels/send", dependencies=[Depends(require_internal_key)])
def send_channel_message(payload: SendDirectIn, db: Session = Depends(get_session)) -> dict:
    """A direct, synchronous send through a tenant's own configured channel - no inbox
    entry, no dedupe key, no retry queue.

    ``/internal/notifications/raise`` is for telling a *platform user* that something
    happened, with an in-app record they can read even if email is off. This is for
    handing a *named recipient* - who may hold no platform account at all - the
    deliverable itself (BR-609's scheduled report export). Reusing the notification
    pipeline for that would mean every export recipient needs an inbox and a mute
    preference, which is the wrong shape for "email this CSV to compliance@vendor.com".

    Synchronous on purpose, matching the caller: a subscription run already retries on
    its own cadence, so a second, independent retry queue here would just be two clocks
    disagreeing about whether a delivery succeeded.
    """
    from .. import channels

    if payload.channel != "email":
        raise AppError("Only the email channel supports a direct send today", 400,
                       "unsupported_channel")

    cfg = db.scalar(select(ChannelConfig).where(
        ChannelConfig.tenant_id == payload.tenant_id, ChannelConfig.channel == "email"))
    if cfg is None or not cfg.enabled:
        return {"ok": False, "detail": "no email channel configured for this tenant"}

    attachment = None
    if payload.attachment_filename and payload.attachment_content_b64:
        import base64
        attachment = (payload.attachment_filename,
                     base64.b64decode(payload.attachment_content_b64),
                     payload.attachment_mime)

    result = channels.send_email(cfg.config or {}, to=payload.to, subject=payload.subject,
                                 body=payload.body, attachment=attachment)
    record_audit(
        service="notification-service", action="notify.direct_send", actor="system",
        actor_role="scheduled-report", tenant_id=payload.tenant_id,
        target_type="channel", target_id="email",
        status="success" if result.ok else "failure",
        detail={"to": payload.to, "detail": result.detail[:300]})
    return {"ok": result.ok, "detail": result.detail}
