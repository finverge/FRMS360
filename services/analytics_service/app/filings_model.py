"""The record of what was generated, from what, and what came back.

Kept because "we filed it" is a claim a bank has to be able to evidence years later, and
because the artefact must be reproducible as it was - not as today's data would render it.
The stored content and its hash are what make that possible; regenerating from live data
would silently reflect every subsequent correction.

Append-mostly: the content is written once and never edited. Only the submission outcome
is added afterwards, when a human comes back with the regulator's acknowledgement.
"""
import uuid
from datetime import datetime

from sqlalchemy import DateTime, Index, Integer, JSON, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from cp_common import Base
from cp_common.schemas_db import CASES


class RegulatoryFiling(Base):
    __tablename__ = "regulatory_filings"
    __table_args__ = (
        Index("ix_filing_case", "tenant_id", "case_id", "kind", "generated_at"),
        Index("ix_filing_status", "tenant_id", "status"),
        {"schema": CASES},
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True,
                                    default=lambda: str(uuid.uuid4()))
    tenant_id: Mapped[str] = mapped_column(String(36), index=True)
    case_id: Mapped[str] = mapped_column(String(40), index=True)
    kind: Mapped[str] = mapped_column(String(8))               # fmr | str
    schema_version: Mapped[str] = mapped_column(String(16))

    #: generated | submitted | acknowledged | superseded
    status: Mapped[str] = mapped_column(String(14), default="generated", index=True)
    #: Monotonic per case and kind, so a corrected refiling is visibly a later one.
    revision: Mapped[int] = mapped_column(Integer, default=1)

    #: The payload exactly as built. Renderings are derived from this on download, so
    #: every format of a given revision says the same thing.
    payload: Mapped[dict] = mapped_column(JSON)
    #: What validation said at the moment of generation - including gaps that were
    #: advisory. Later data changes must not rewrite that history.
    validation: Mapped[dict] = mapped_column(JSON, default=dict)
    content_hash: Mapped[str] = mapped_column(String(64), index=True)

    generated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),
                                                   server_default=func.now())
    generated_by: Mapped[str] = mapped_column(String(255))

    #: Filled in when someone confirms they actually submitted it.
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True),
                                                          nullable=True)
    submitted_by: Mapped[str] = mapped_column(String(255), default="", server_default="")
    #: The regulator's own reference. This is the thing an inspection asks for.
    reference_number: Mapped[str] = mapped_column(String(120), default="",
                                                  server_default="")
    ack_note: Mapped[str] = mapped_column(Text, default="", server_default="")
