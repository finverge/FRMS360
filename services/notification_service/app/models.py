"""Notifications, their delivery attempts, and who wants what.

The design decision that shapes everything here is **deduplication by key**. A compliance
clock is a state, not an event: a case is either inside its response window or past it.
Notifying from state rather than from an event stream means nothing is lost when a worker
is down - it catches up on the next pass - but it also means the same condition is
observed on every pass. The ``dedupe_key`` is what turns "this case is overdue" into one
notification rather than one every five minutes.

Delivery is recorded per channel, separately from the notification itself. An email that
bounces must not delete the item from someone's inbox, and a webhook a bank's firewall
rejected must be visibly retriable rather than silently gone.
"""
import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean, DateTime, Index, Integer, JSON, String, Text, UniqueConstraint, func,
)
from sqlalchemy.orm import Mapped, mapped_column

from cp_common import Base
from cp_common.schemas_db import NOTIFY


def _uuid() -> str:
    return str(uuid.uuid4())


class Notification(Base):
    __tablename__ = "notifications"
    __table_args__ = (
        # The heart of the design: one notification per (tenant, recipient, condition).
        UniqueConstraint("tenant_id", "recipient", "dedupe_key", name="uq_notify_dedupe"),
        Index("ix_notify_inbox", "tenant_id", "recipient", "read_at", "created_at"),
        {"schema": NOTIFY},
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    tenant_id: Mapped[str] = mapped_column(String(36), index=True)
    #: The user's email. Roles are resolved to people before anything is stored, so an
    #: inbox is always a person's inbox.
    recipient: Mapped[str] = mapped_column(String(255), index=True)
    #: Machine-readable class, e.g. case_assigned, nj_window_closing, fmr_overdue.
    kind: Mapped[str] = mapped_column(String(40), index=True)
    #: Stable identity of the *condition*, not the moment. "fmr_overdue:C123" fires once.
    dedupe_key: Mapped[str] = mapped_column(String(120))
    severity: Mapped[str] = mapped_column(String(10), default="info")  # info|warn|urgent
    subject: Mapped[str] = mapped_column(String(200))
    body: Mapped[str] = mapped_column(Text, default="", server_default="")
    #: Deep link into the console, so acting on it is one click rather than a search.
    link: Mapped[str] = mapped_column(String(255), default="", server_default="")
    context: Mapped[dict] = mapped_column(JSON, default=dict)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),
                                                 server_default=func.now(), index=True)
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True),
                                                     nullable=True)
    #: Set when the underlying condition clears - an overdue filing that gets filed.
    #: Resolved items leave the inbox without anyone having to dismiss them.
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True),
                                                         nullable=True)


class Delivery(Base):
    """One attempt to push a notification down one channel."""

    __tablename__ = "deliveries"
    __table_args__ = (
        Index("ix_delivery_pending", "status", "next_attempt_at"),
        {"schema": NOTIFY},
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    notification_id: Mapped[str] = mapped_column(String(36), index=True)
    tenant_id: Mapped[str] = mapped_column(String(36), index=True)
    channel: Mapped[str] = mapped_column(String(16))           # email | webhook
    target: Mapped[str] = mapped_column(String(255))
    #: pending | sent | abandoned. A delivery that has failed but has attempts left
    #: stays *pending* with attempts > 0 - there is no separate "failed" state, because
    #: a failure that will be retried is not a final outcome.
    status: Mapped[str] = mapped_column(String(12), default="pending", index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    last_error: Mapped[str] = mapped_column(Text, default="", server_default="")
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True),
                                                             nullable=True)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True),
                                                     nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),
                                                 server_default=func.now())


class ChannelConfig(Base):
    """How a tenant wants notifications delivered.

    Per tenant because a bank's mail relay is inside its own network, and because some
    will want everything routed to their own incident system instead of to inboxes.
    """

    __tablename__ = "channel_config"
    __table_args__ = (
        UniqueConstraint("tenant_id", "channel", name="uq_channel_per_tenant"),
        {"schema": NOTIFY},
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    tenant_id: Mapped[str] = mapped_column(String(36), index=True)
    channel: Mapped[str] = mapped_column(String(16))            # email | webhook
    enabled: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    #: SMTP host/port/user/from, or the webhook URL and headers. Secrets are never
    #: returned by the API that reads this.
    config: Mapped[dict] = mapped_column(JSON, default=dict)
    #: Only notifications at or above this level leave the platform. In-app always shows
    #: everything; nobody wants an email for each of forty routine alerts.
    min_severity: Mapped[str] = mapped_column(String(10), default="warn",
                                              server_default="warn")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),
                                                 server_default=func.now())


class Preference(Base):
    """A person's own opt-outs, within what their tenant allows.

    Deliberately opt-*out*: a fraud analyst who never configured anything still gets
    told their queue has a critical alert in it.
    """

    __tablename__ = "preferences"
    __table_args__ = (
        UniqueConstraint("tenant_id", "recipient", name="uq_pref_recipient"),
        {"schema": NOTIFY},
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    tenant_id: Mapped[str] = mapped_column(String(36), index=True)
    recipient: Mapped[str] = mapped_column(String(255), index=True)
    email_enabled: Mapped[bool] = mapped_column(Boolean, default=True,
                                                server_default="true")
    #: Kinds this person has muted. Compliance-critical kinds ignore this - see
    #: notification_service.rules.UNMUTABLE.
    muted_kinds: Mapped[dict] = mapped_column(JSON, default=dict)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),
                                                 server_default=func.now())
