"""The board / ACB pack as a retained record (BR-507).

A dashboard answers "what is the position now". A board pack has to answer something
harder: **what was the board actually told, on the day it was told.** The minutes of a
meeting refer to it, and eighteen months later a supervisor may ask whether the committee
was shown a fraud that had already been declared. Re-running today's query cannot answer
that - the numbers will have moved.

So a pack is a frozen snapshot with a content hash, not a saved filter. Three consequences:

* **The figures are stored, not recomputed on view.** Downloading a pack from last quarter
  renders the payload as it was built.
* **Issuing is a separate act from generating.** A draft can be regenerated freely; once
  issued to the committee it is superseded rather than overwritten, because it is now a
  governance record.
* **Distribution is recorded per recipient.** "Sent to the ACB" is not evidence. Who, when,
  and whether the channel actually accepted it, is.
"""
import uuid
from datetime import datetime

from sqlalchemy import (
    DateTime, Index, Integer, String, Text, UniqueConstraint, func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from cp_common import Base
from cp_common.schemas_db import ANALYTICS

#: draft    - generated, may be regenerated at will
#: issued   - put in front of the committee; frozen
#: superseded - an earlier draft replaced by a newer generation
STATUSES = ("draft", "issued", "superseded")


class BoardPack(Base):
    __tablename__ = "board_packs"
    __table_args__ = (
        Index("ix_board_pack_tenant_period", "tenant_id", "period_start"),
        Index("ix_board_pack_tenant_status", "tenant_id", "status"),
        {"schema": ANALYTICS},
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True,
                                    default=lambda: str(uuid.uuid4()))
    tenant_id: Mapped[str] = mapped_column(String(36), index=True)
    #: Human label for the period, e.g. "Q1 FY2026-27". Stored rather than derived so a
    #: change to the labelling convention cannot retitle a pack already issued.
    period_label: Mapped[str] = mapped_column(String(60))
    period_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    period_end: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    cadence: Mapped[str] = mapped_column(String(16), default="quarterly")
    status: Mapped[str] = mapped_column(String(12), default="draft", index=True)
    #: Every section's figures, as built. The pack renders from this, never from a query.
    payload: Mapped[dict] = mapped_column(JSONB)
    content_hash: Mapped[str] = mapped_column(String(64))
    revision: Mapped[int] = mapped_column(Integer, default=1)
    generated_by: Mapped[str] = mapped_column(String(255))
    generated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),
                                                   server_default=func.now())
    issued_by: Mapped[str] = mapped_column(String(255), default="", server_default="")
    issued_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True),
                                                       nullable=True)
    #: What the committee was told about the period in the chair's own words. Optional:
    #: the figures stand on their own, but a pack with no narrative is rarely useful.
    note: Mapped[str] = mapped_column(Text, default="", server_default="")


class BoardPackDistribution(Base):
    """One row per recipient per issue. "Circulated to the ACB" is not evidence."""

    __tablename__ = "board_pack_distributions"
    __table_args__ = (
        UniqueConstraint("pack_id", "recipient", name="uq_pack_recipient"),
        Index("ix_pack_dist_tenant", "tenant_id", "pack_id"),
        {"schema": ANALYTICS},
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True,
                                    default=lambda: str(uuid.uuid4()))
    tenant_id: Mapped[str] = mapped_column(String(36), index=True)
    pack_id: Mapped[str] = mapped_column(String(36), index=True)
    recipient: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(60), default="", server_default="")
    #: sent | failed | not_configured. The last is the honest outcome when no channel is
    #: wired: the pack exists and nobody received it, which must not read as success.
    status: Mapped[str] = mapped_column(String(20), default="not_configured")
    detail: Mapped[str] = mapped_column(Text, default="", server_default="")
    sent_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),
                                              server_default=func.now())
