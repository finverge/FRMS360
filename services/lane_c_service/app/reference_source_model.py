"""Where a Lane C reference feed is fetched from, and on what cadence.

A scoped-down mirror of analytics_service/app/reference_source_model.py's shape and
philosophy, in the lane_c schema rather than analytics - schema isolation means Lane C
cannot read the Lane B version of this table even if it wanted to, and MCA/ROC filings
and rating actions are periodic borrower facts, not per-transaction screening data, so
they belong with Lane C's own periodic review anyway, not bolted onto Lane B's
transaction-screening mechanism.

Same guard as the Lane B version, and for the same reason: growing is never suspicious,
shrinking is - a fetch that drops most of its entries is refused, not silently loaded.
"""
import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean, DateTime, Float, Index, Integer, String, Text, UniqueConstraint, func,
)
from sqlalchemy.orm import Mapped, mapped_column

from cp_common import Base
from cp_common.crypto import EncryptedSecret
from cp_common.schemas_db import LANE_C

#: Deliberately small, same reasoning as the Lane B FORMATS constant: a vendor's feed is
#: either a line-per-entry text file, a CSV, or JSON. Anything else is an integration.
FORMATS = ("lines", "csv", "json")

#: Refuse a load that drops more than this fraction of the previous version's entries.
DEFAULT_MAX_SHRINK = 0.10


class LaneCReferenceSource(Base):
    __tablename__ = "reference_sources"
    __table_args__ = (
        UniqueConstraint("tenant_id", "kind", name="uq_lane_c_reference_source_kind"),
        Index("ix_lane_c_reference_source_due", "enabled", "next_due_at"),
        {"schema": LANE_C},
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True,
                                    default=lambda: str(uuid.uuid4()))
    tenant_id: Mapped[str] = mapped_column(String(36), index=True)
    #: One of reference_model.LANE_C_LIST_KINDS.
    kind: Mapped[str] = mapped_column(String(32), index=True)
    url: Mapped[str] = mapped_column(String(600))
    fmt: Mapped[str] = mapped_column(String(8), default="lines", server_default="lines")
    #: For a feed behind a token. Sealed at rest like every other credential.
    auth_header: Mapped[str] = mapped_column(String(64), default="", server_default="")
    auth_value: Mapped[str] = mapped_column(
        EncryptedSecret("lane_c.reference_source_auth"), default="", server_default="")
    #: Which field carries the borrower's key (CIN/PAN), when the format has fields.
    key_field: Mapped[str] = mapped_column(String(64), default="", server_default="")
    cadence_hours: Mapped[int] = mapped_column(Integer, default=24)
    max_shrink: Mapped[float] = mapped_column(Float, default=DEFAULT_MAX_SHRINK,
                                              server_default="0.10")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")

    last_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True),
                                                             nullable=True)
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True),
                                                             nullable=True)
    last_checksum: Mapped[str] = mapped_column(String(64), default="", server_default="")
    last_entry_count: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str] = mapped_column(Text, default="", server_default="")
    next_due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True),
                                                         nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),
                                                 server_default=func.now())
