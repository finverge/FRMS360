"""The inbound record.

Kept separately from ``analytics.fact_transaction`` on purpose. The fact table is the
*derived* view detection and dashboards work from; this is what the bank actually sent,
byte for byte, with the time we received it. When a figure is challenged months later,
"what did you receive and when" is a different question from "what did you conclude", and
answering it from a table that detection also rewrites is not an answer.

Append-only. There is no update path and no delete endpoint; the projection marks progress
via ``state`` and never edits ``payload``.
"""
import uuid
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Index, JSON, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from cp_common import Base
from cp_common.schemas_db import INGESTION


class RawTransaction(Base):
    __tablename__ = "raw_transaction"
    __table_args__ = (
        # A rail's own identifier is the natural dedupe key. Replayed files and retried
        # batches are normal operations, not incidents, and must not double-count value.
        UniqueConstraint("tenant_id", "rail", "source_txn_id", name="uq_raw_txn_source"),
        Index("ix_raw_txn_pending", "tenant_id", "state", "received_at"),
        {"schema": INGESTION},
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True,
                                    default=lambda: str(uuid.uuid4()))
    tenant_id: Mapped[str] = mapped_column(String(36), index=True)
    rail: Mapped[str] = mapped_column(String(8), index=True)
    source_txn_id: Mapped[str] = mapped_column(String(64), index=True)
    #: Exactly what arrived, before adaptation.
    payload: Mapped[dict] = mapped_column(JSON)
    #: The canonical form the adapter produced, so a mapping change is auditable.
    canonical: Mapped[dict] = mapped_column(JSON)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    amount_paise: Mapped[int] = mapped_column(BigInteger)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),
                                                  server_default=func.now(), index=True)
    #: pending | claimed | projected | rejected
    state: Mapped[str] = mapped_column(String(12), default="pending", index=True)
    #: Set while a detection worker holds it, so two workers cannot process it twice.
    claim_token: Mapped[str] = mapped_column(String(36), default="", server_default="")
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error: Mapped[str] = mapped_column(Text, default="", server_default="")
    #: Where this came from, so the demo corpus stays distinguishable from live traffic.
    source: Mapped[str] = mapped_column(String(16), default="live", server_default="live")
