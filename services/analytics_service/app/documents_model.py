"""Case documents - the natural-justice paper trail.

RBI requires that a borrower be given a reasonable opportunity to respond before an
account is classified as fraud, and that the decision be a *reasoned order*
(SBI v. Rajesh Agarwal). Dates alone do not evidence that: a case can carry a show-cause
timestamp with nothing behind it. The documents themselves are the evidence.

Two deliberate choices:

* **No delete endpoint.** A compliance trail that can be quietly pruned is not a trail.
  Superseding a document means uploading a newer one; both remain.
* **A SHA-256 is stored with every upload**, so a document produced during an inspection
  can be shown to be the one that was filed.
"""
import hashlib
import uuid
from datetime import datetime

from sqlalchemy import DateTime, Index, Integer, LargeBinary, String, func
from sqlalchemy.orm import Mapped, mapped_column

from cp_common import Base
from cp_common.schemas_db import CASES

# Document kinds that carry regulatory meaning, plus a catch-all.
DOC_TYPES = [
    "show_cause_notice",   # putting the borrower on notice
    "borrower_response",   # what they said
    "reasoned_order",      # the decision with reasons - the natural-justice artefact
    "lea_referral",        # law-enforcement referral
    "fmr_filing",          # the return as submitted
    "other",
]

# Kept small on purpose: this is an evidence store, not a document management system.
MAX_BYTES = 10 * 1024 * 1024


class CaseDocument(Base):
    __tablename__ = "case_documents"
    __table_args__ = (Index("ix_case_doc_tenant_case", "tenant_id", "case_id"),
                      {"schema": CASES})

    id: Mapped[str] = mapped_column(String(36), primary_key=True,
                                    default=lambda: str(uuid.uuid4()))
    tenant_id: Mapped[str] = mapped_column(String(36), index=True)
    case_id: Mapped[str] = mapped_column(String(40), index=True)
    doc_type: Mapped[str] = mapped_column(String(32), index=True)
    filename: Mapped[str] = mapped_column(String(255))
    content_type: Mapped[str] = mapped_column(String(120),
                                              default="application/octet-stream")
    size_bytes: Mapped[int] = mapped_column(Integer, default=0)
    # Proves the file produced later is the file that was filed.
    sha256: Mapped[str] = mapped_column(String(64))
    note: Mapped[str] = mapped_column(String(500), default="", server_default="")
    uploaded_by: Mapped[str] = mapped_column(String(255))
    uploaded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),
                                                  server_default=func.now())
    content: Mapped[bytes] = mapped_column(LargeBinary)


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()
