"""Lane C's own reference data: the periodic, borrower-level facts a filed financial
statement alone can never carry.

A scoped-down mirror of analytics_service/app/reference_model.py's LIST_KINDS /
ReferenceList / ReferenceEntry shape, in the lane_c schema. Not a shared import across
services - schema isolation (cp_common.schemas_db) means lane-c-service's database role
cannot reach analytics-service's tables at all, and these two kinds are periodic
borrower facts (a company's own ROC filing history, its external credit rating), not
per-transaction screening data, so they belong with Lane C's quarterly review cadence,
not bolted onto Lane B's payment-screening mechanism. See docs/rbi_ews_mapping.py's
notes for #18 and this file's own LANE_C_LIST_KINDS "why" text for what each answers.

Same three properties as the Lane B version, for the same reasons: versioned and never
edited in place (an alert must be traceable to the exact list version that matched,
months later); an absent list is unmeasurable, never treated as "nothing to report";
loading is a decision (see reference_fetch.py's shrink check), not "whatever showed up
at the URL wins".
"""
import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Index, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from cp_common import Base
from cp_common.schemas_db import LANE_C

#: A closed set, same discipline as Lane B's LIST_KINDS: the kind drives how entries are
#: matched, and both are keyed by an exact borrower identifier (CIN/PAN), never a fuzzy
#: name match - a periodic borrower fact must land on the right company, not the closest
#: one.
LANE_C_LIST_KINDS = {
    "mca_roc": {
        "label": "MCA / ROC filings",
        "why": "Liabilities in a borrower's Registrar-of-Companies search report that "
               "its own financial statement does not disclose - RBI 2016 illustrative "
               "signal #18, otherwise entirely uncovered.",
        "feeds": ("LNC-10",),
        "match": "key",
    },
    "rating_action": {
        "label": "Credit rating actions",
        "why": "A negative rating action (downgrade, or outlook turned negative) since "
               "the borrower's last Lane C review - not an RBI 2016 signal, added value "
               "beyond the illustrative list.",
        "feeds": ("LNC-11",),
        "match": "key",
    },
    "insurance_coverage": {
        "label": "Inventory insurance coverage",
        "why": "Under- or over-insured inventory (RBI #7) - cross-checked against the "
               "INVENTORY figure Lane C already extracts from the borrower's own "
               "statement, so no new document parsing is needed on Lane C's side.",
        "feeds": ("LNC-17",),
        "match": "key",
    },
    "stock_audit": {
        "label": "Stock audit findings",
        "why": "A critical issue in a third-party stock auditor's report (RBI #17) - "
               "same shape as mca_roc, a boolean finding plus detail.",
        "feeds": ("LNC-18",),
        "match": "key",
    },
    "shareholding": {
        "label": "Promoter shareholding pattern",
        "why": "Reduction in promoter stake or increase in encumbered shares (RBI #41) "
               "- depository (NSDL/CDSL) data, compared against Lane C's own record of "
               "the prior review's shareholding to detect a reduction, the same "
               "before/after shape rating_action uses for a downgrade.",
        "feeds": ("LNC-19",),
        "match": "key",
    },
    "enforcement_action": {
        "label": "Regulatory enforcement action",
        "why": "A raid or enforcement action by Income Tax/GST/TDS/central excise "
               "authorities (RBI #40) - unlike the other kinds here, presence of any "
               "matching entry at all is the finding; there is no boolean sub-field to "
               "read, since the feed publishing an entry for this borrower already means "
               "an action is on record.",
        "feeds": ("LNC-20",),
        "match": "key",
    },
    "management_changes": {
        "label": "MCA director/KMP changes",
        "why": "Resignation of key managerial personnel, or frequent change in "
               "management (RBI #42) - MCA's own DIR-12/KMP filing history, a different "
               "fact from mca_roc's undisclosed-liability search and so a separate kind "
               "rather than an overload of it.",
        "feeds": ("LNC-23",),
        "match": "key",
    },
}


class LaneCReferenceList(Base):
    """One loaded version of one list."""

    __tablename__ = "reference_lists"
    __table_args__ = (
        UniqueConstraint("tenant_id", "kind", "version", name="uq_lane_c_reflist_version"),
        Index("ix_lane_c_reflist_active", "tenant_id", "kind", "active"),
        {"schema": LANE_C},
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True,
                                    default=lambda: str(uuid.uuid4()))
    tenant_id: Mapped[str] = mapped_column(String(36), index=True)
    kind: Mapped[str] = mapped_column(String(32), index=True)
    #: The publisher's own version label, not a serial invented here.
    version: Mapped[str] = mapped_column(String(80))
    source: Mapped[str] = mapped_column(String(200), default="", server_default="")
    #: Exactly one version per (tenant, kind) is active - flipped in one statement in
    #: reference_fetch.refresh(), same as the Lane B version.
    active: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    entry_count: Mapped[int] = mapped_column(Integer, default=0)
    checksum: Mapped[str] = mapped_column(String(64), default="", server_default="")
    loaded_by: Mapped[str] = mapped_column(String(255))
    loaded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),
                                                server_default=func.now())
    note: Mapped[str] = mapped_column(Text, default="", server_default="")


class LaneCReferenceEntry(Base):
    """One row of a list, keyed by a borrower's CIN/PAN."""

    __tablename__ = "reference_entries"
    __table_args__ = (
        Index("ix_lane_c_refentry_list_key", "list_id", "match_key"),
        Index("ix_lane_c_refentry_tenant", "tenant_id", "kind"),
        {"schema": LANE_C},
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True,
                                    default=lambda: str(uuid.uuid4()))
    tenant_id: Mapped[str] = mapped_column(String(36), index=True)
    list_id: Mapped[str] = mapped_column(String(36), index=True)
    kind: Mapped[str] = mapped_column(String(32))
    #: Normalised CIN/PAN - upper-case, punctuation stripped, same normalise() shape
    #: as Lane B's, kept as a small local copy rather than a cross-service import.
    match_key: Mapped[str] = mapped_column(String(64))
    display_name: Mapped[str] = mapped_column(String(300))
    #: Everything else the feed gave: undisclosed liabilities, rating grade/outlook, etc.
    attributes: Mapped[dict] = mapped_column(JSONB, default=dict)
