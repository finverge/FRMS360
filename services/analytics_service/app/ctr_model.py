"""The record of a Cash Transaction Report generated for one account, one month.

Same append-mostly discipline as ``filings_model.RegulatoryFiling`` - the content is
written once and never edited, only the submission outcome is added later - but this is
a genuinely different shape, not a variant of the same table.

**Why not just another ``kind`` on RegulatoryFiling.** An FMR or STR is about one case:
a specific fraud, investigated, evidenced, declared. A CTR is not about a case at all -
PMLA Rule 3 requires it whether or not anything suspicious happened, for any account whose
cash movement crossed the threshold in a calendar month. Storing that under a table whose
primary key is ``case_id`` would mean either inventing a case that was never opened, or
loosening ``case_id`` to mean "case, or account, or whatever else shows up next" - the
kind of small dishonesty that a query written a year from now trusts by accident.
"""
import uuid
from datetime import datetime

from sqlalchemy import DateTime, Index, Integer, JSON, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from cp_common import Base
from cp_common.schemas_db import ANALYTICS


class CtrFiling(Base):
    __tablename__ = "ctr_filings"
    __table_args__ = (
        # One filing per (tenant, account, period) per revision chain - a corrected
        # refiling supersedes rather than duplicates, exactly as FMR/STR do.
        Index("ix_ctr_account_period", "tenant_id", "account", "period"),
        Index("ix_ctr_status", "tenant_id", "status"),
        UniqueConstraint("tenant_id", "account", "period", "revision",
                         name="uq_ctr_account_period_revision"),
        {"schema": ANALYTICS},
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True,
                                    default=lambda: str(uuid.uuid4()))
    tenant_id: Mapped[str] = mapped_column(String(36), index=True)
    account: Mapped[str] = mapped_column(String(40), index=True)
    #: Calendar month the aggregation covers, "YYYY-MM" - matches PMLA Rule 3's monthly
    #: aggregation window, not an arbitrary reporting cycle.
    period: Mapped[str] = mapped_column(String(7), index=True)
    schema_version: Mapped[str] = mapped_column(String(16))

    #: generated | acknowledged | superseded
    status: Mapped[str] = mapped_column(String(14), default="generated", index=True)
    revision: Mapped[int] = mapped_column(Integer, default=1)

    payload: Mapped[dict] = mapped_column(JSON)
    content_hash: Mapped[str] = mapped_column(String(64), index=True)

    generated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),
                                                   server_default=func.now())
    generated_by: Mapped[str] = mapped_column(String(255))

    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True),
                                                          nullable=True)
    submitted_by: Mapped[str] = mapped_column(String(255), default="", server_default="")
    reference_number: Mapped[str] = mapped_column(String(120), default="",
                                                  server_default="")
    ack_note: Mapped[str] = mapped_column(Text, default="", server_default="")
