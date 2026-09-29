"""Scheduled report subscriptions.

A board pack that only exists when someone remembers to log in and export it is not a
governance control. A subscription is a standing instruction: this view, this cadence,
these recipients.

**Delivery is pluggable and honest about what is wired.** Rendering a report and
*delivering* it are different problems: the first is ours, the second needs a mail relay
this deployment does not have. So a run always produces the artefact and records the
attempt; the `spool` driver writes it to disk, and `email` reports itself unconfigured
rather than silently discarding a report someone believes was sent.
"""
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import Boolean, DateTime, Index, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from cp_common import Base
from cp_common.schemas_db import ANALYTICS

CADENCES = {"daily": 1, "weekly": 7, "monthly": 30}
FORMATS = ["csv"]
DRIVERS = ["spool", "email"]


class ReportSubscription(Base):
    __tablename__ = "report_subscriptions"
    __table_args__ = (
        UniqueConstraint("tenant_id", "owner", "name", name="uq_subscription_name"),
        Index("ix_subscription_tenant_active", "tenant_id", "active"),
        {"schema": ANALYTICS},
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True,
                                    default=lambda: str(uuid.uuid4()))
    tenant_id: Mapped[str] = mapped_column(String(36), index=True)
    owner: Mapped[str] = mapped_column(String(255), index=True)
    name: Mapped[str] = mapped_column(String(120))
    # What to send: the dataset plus the same filter query a saved view holds.
    entity: Mapped[str] = mapped_column(String(20), default="case")
    query: Mapped[str] = mapped_column(Text, default="")
    fmt: Mapped[str] = mapped_column(String(8), default="csv")
    cadence: Mapped[str] = mapped_column(String(12), default="weekly")
    driver: Mapped[str] = mapped_column(String(12), default="spool")
    recipients: Mapped[str] = mapped_column(Text, default="")
    active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True),
                                                         nullable=True)
    last_status: Mapped[str] = mapped_column(String(240), default="", server_default="")
    run_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),
                                                 server_default=func.now())

    def is_due(self, now: datetime | None = None) -> bool:
        now = now or datetime.now(timezone.utc)
        if not self.active:
            return False
        if self.last_run_at is None:
            return True
        return now - self.last_run_at >= timedelta(days=CADENCES.get(self.cadence, 7))
