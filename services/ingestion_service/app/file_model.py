"""Batch file intake: what arrived, what was taken, and what was refused.

Banks do not send transactions over an API at night. They drop a file on an SFTP landing
zone and expect the platform to have processed it by morning, and to be able to say
exactly what happened to it if a figure is queried a quarter later.

Four things this has to get right, each of which is a way real batch intake fails:

* **A file being uploaded is not a file that has arrived.** SFTP writes are not atomic;
  a watcher that picks up a file the moment it appears will happily ingest the first
  half of it. Intake therefore waits for an explicit sidecar marker rather than guessing
  from size or mtime.

* **The same file delivered twice must not double-count value.** Redelivery is a normal
  operation - a network blip, an operator rerun - not an incident. The file's SHA-256 is
  the key, so a byte-identical redelivery is recognised as such before a single row is
  read. Row-level uniqueness stays as the backstop for files that overlap partially.

* **A bad row must not cost the good rows.** A 50,000-row file with nine malformed
  timestamps is 49,991 usable transactions and nine questions. The nine go to quarantine
  with their line number and the reason, so the bank can fix and resend those rows rather
  than resending the file and relying on dedupe.

* **Nothing is deleted to signal success.** The file is moved, never removed, and the
  record of it stays whatever happens to the filesystem.
"""
import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger, DateTime, Index, Integer, JSON, String, Text, UniqueConstraint, func,
)
from sqlalchemy.orm import Mapped, mapped_column

from cp_common import Base
from cp_common.schemas_db import INGESTION

#: received    - seen and registered, not yet read
#: processing  - rows being adapted
#: completed   - every row either accepted, duplicate or quarantined
#: failed      - the file could not be read at all (bad encoding, no header, unreadable)
#: duplicate   - byte-identical to a file already processed for this tenant
STATES = ("received", "processing", "completed", "failed", "duplicate")

#: The marker that says an upload has finished. Chosen over mtime-stability heuristics
#: because it is explicit: the sender says when they are done, rather than the receiver
#: guessing and occasionally guessing wrong at 3am.
DONE_SUFFIX = ".done"


class IngestFile(Base):
    __tablename__ = "ingest_files"
    __table_args__ = (
        # The dedupe key. Same bytes, same tenant, already handled.
        UniqueConstraint("tenant_id", "sha256", name="uq_ingest_file_digest"),
        Index("ix_ingest_file_tenant_state", "tenant_id", "state", "received_at"),
        {"schema": INGESTION},
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True,
                                    default=lambda: str(uuid.uuid4()))
    tenant_id: Mapped[str] = mapped_column(String(36), index=True)
    filename: Mapped[str] = mapped_column(String(400))
    #: Of the file's bytes, not of the parsed rows: it has to be computable before the
    #: file is understood, so an unreadable redelivery is still recognised.
    sha256: Mapped[str] = mapped_column(String(64), index=True)
    size_bytes: Mapped[int] = mapped_column(BigInteger, default=0)
    rail: Mapped[str] = mapped_column(String(8), default="", server_default="")
    state: Mapped[str] = mapped_column(String(12), default="received", index=True)
    rows_total: Mapped[int] = mapped_column(Integer, default=0)
    rows_accepted: Mapped[int] = mapped_column(Integer, default=0)
    rows_duplicate: Mapped[int] = mapped_column(Integer, default=0)
    rows_quarantined: Mapped[int] = mapped_column(Integer, default=0)
    #: Why the file as a whole failed, when it did.
    error: Mapped[str] = mapped_column(Text, default="", server_default="")
    #: Where it was moved to once handled, so the operator can find it.
    archived_path: Mapped[str] = mapped_column(String(600), default="",
                                               server_default="")
    #: If this is a redelivery, the file it duplicates.
    duplicate_of: Mapped[str | None] = mapped_column(String(36), nullable=True)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),
                                                  server_default=func.now(), index=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True),
                                                        nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True),
                                                         nullable=True)
    source: Mapped[str] = mapped_column(String(16), default="file", server_default="file")


class QuarantinedRow(Base):
    """A row that could not be taken, kept with enough context to fix and resend it."""

    __tablename__ = "quarantined_rows"
    __table_args__ = (
        Index("ix_quarantine_file", "tenant_id", "file_id"),
        {"schema": INGESTION},
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True,
                                    default=lambda: str(uuid.uuid4()))
    tenant_id: Mapped[str] = mapped_column(String(36), index=True)
    file_id: Mapped[str] = mapped_column(String(36), index=True)
    #: 1-based line number *in the file*, header included, because that is what the
    #: operator will be looking at when they open it.
    line_number: Mapped[int] = mapped_column(Integer)
    #: The row exactly as it arrived. Kept so the failure is reproducible.
    raw: Mapped[dict] = mapped_column(JSON)
    reason: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),
                                                 server_default=func.now())
