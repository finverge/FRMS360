"""Where a reference list is fetched from, and on what cadence (BR-212).

Loading a sanctions list by hand works once. It does not survive contact with a bank's
operating reality, where the list is republished weekly and the person who used to upload
it has changed teams. A list that is six months stale screens against yesterday's
designations and reports clean, which is the failure this exists to prevent.

**The guard that matters is not the schedule; it is the shrink check.** A published
sanctions list that arrives with 90% of its entries missing is a broken feed - a
half-written file, a truncated response, an authentication page served with a 200 - and
loading it would silently disarm screening across the whole tenant. A fetch that shrinks
the list beyond a configured tolerance is refused and reported, and the previous version
stays active. Growing is never suspicious; shrinking is.
"""
import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean, DateTime, Float, Index, Integer, String, Text, UniqueConstraint, func,
)
from sqlalchemy.orm import Mapped, mapped_column

from cp_common import Base
from cp_common.crypto import EncryptedSecret
from cp_common.schemas_db import ANALYTICS

#: How a fetched document is understood. Deliberately small: a bank's feed is either a
#: line-per-entry text file, a CSV, or JSON. Anything else is an integration, not config.
FORMATS = ("lines", "csv", "json")

#: Refuse a load that drops more than this fraction of the previous version's entries.
#: 0.10 is deliberately tight for a screening list - real designation lists move by a
#: handful of entries at a time, not by thousands.
DEFAULT_MAX_SHRINK = 0.10


class ReferenceSource(Base):
    __tablename__ = "reference_sources"
    __table_args__ = (
        UniqueConstraint("tenant_id", "kind", name="uq_reference_source_kind"),
        Index("ix_reference_source_due", "enabled", "next_due_at"),
        {"schema": ANALYTICS},
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True,
                                    default=lambda: str(uuid.uuid4()))
    tenant_id: Mapped[str] = mapped_column(String(36), index=True)
    #: One of reference_model.LIST_KINDS.
    kind: Mapped[str] = mapped_column(String(32), index=True)
    url: Mapped[str] = mapped_column(String(600))
    fmt: Mapped[str] = mapped_column(String(8), default="lines", server_default="lines")
    #: For a feed behind a token. Sealed at rest like every other credential.
    auth_header: Mapped[str] = mapped_column(String(64), default="",
                                             server_default="")
    auth_value: Mapped[str] = mapped_column(
        EncryptedSecret("analytics.reference_source_auth"), default="",
        server_default="")
    #: Which field carries the name / key, when the format has fields.
    key_field: Mapped[str] = mapped_column(String(64), default="", server_default="")
    cadence_hours: Mapped[int] = mapped_column(Integer, default=24)
    #: Fraction of entries that may disappear between versions before a load is refused.
    max_shrink: Mapped[float] = mapped_column(Float, default=DEFAULT_MAX_SHRINK,
                                              server_default="0.10")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")

    last_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True),
                                                             nullable=True)
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True),
                                                             nullable=True)
    #: Of the last document fetched. An unchanged digest means no new version is created,
    #: so a daily poll of a weekly list costs one request and nothing else.
    last_checksum: Mapped[str] = mapped_column(String(64), default="",
                                               server_default="")
    last_entry_count: Mapped[int] = mapped_column(Integer, default=0)
    #: Why the last attempt did not produce a version. Empty on success.
    last_error: Mapped[str] = mapped_column(Text, default="", server_default="")
    next_due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True),
                                                         nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),
                                                 server_default=func.now())
