"""Tenant reference data: the lists detection needs but payments do not carry.

Some EWS indicators cannot be answered from the transaction stream at any cost. "Is this
counterparty on a sanctions list" needs the list; "is this collateral already charged to
another lender" needs the charge registry. Until now those indicators sat in the dormant
register with an explanation, which was honest but not useful.

Three properties matter more than the storage:

* **Versioned, never edited in place.** A sanctions list is evidence. When an alert fires
  the bank must be able to say *which version of which list* matched, months later, even
  though the list has been replaced fifty times since. So a load creates a new version and
  flips the active pointer; the old version stays.

* **An absent list is not an empty list.** If no sanctions list has ever been loaded,
  CPT-02 is *unmeasurable* and says so. Treating "no list" as "no matches" would report
  perfect sanctions compliance to a bank that had never uploaded a list - the single most
  dangerous silent failure available in this domain.

* **Matching is recorded, not just scored.** The alert stores what matched and how well,
  because a sanctions hit gets reviewed by a human who needs to see the matched name.
"""
import uuid
from datetime import date, datetime

from sqlalchemy import (
    Boolean, Date, DateTime, Index, Integer, String, Text, UniqueConstraint, func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from cp_common import Base
from cp_common.schemas_db import ANALYTICS

#: What a list is for. The kind drives how entries are matched, so it is a closed set.
LIST_KINDS = {
    "sanctions": {
        "label": "Sanctions / designated persons",
        "why": "UNSC, MHA and OFAC designations screened against counterparty names.",
        "feeds": ("CPT-02",),
        # Name matching is fuzzy; account matching is exact.
        "match": "name",
    },
    "negative_list": {
        "label": "Internal negative list",
        "why": "Accounts and parties the bank has itself flagged - previously defrauded, "
               "closed for cause, or subject to an internal alert.",
        "feeds": ("CPT-02",),
        "match": "name",
    },
    "pep": {
        "label": "Politically exposed persons",
        "why": "Enhanced due diligence under the PMLA rules.",
        "feeds": ("CPT-02",),
        "match": "name",
    },
    "cfr": {
        "label": "RBI Central Fraud Registry (BR-509)",
        "why": "Parties already reported as fraud elsewhere in the banking system - the "
               "database this bank's own FMR filings feed (Directions, Chapter VI-B). "
               "Unlike a sanctions list, RBI does not publish CFR as a downloadable bulk "
               "feed - access is per-user portal credentials (userid/password) for "
               "case-by-case lookups, not a file this platform can poll on a schedule. "
               "So this stays honestly unloaded - unmeasurable, not clean - until a "
               "tenant's own compliance team loads what its portal lookups found, "
               "through this same manual entry point every other list uses.",
        "feeds": ("CPT-02",),
        "match": "name",
    },
    "group_register": {
        "label": "Connected / group companies",
        "why": "Lending to a borrower's own group, dressed as an arm's-length "
               "transaction, is the shape CBS-03 looks for.",
        "feeds": ("CBS-03",),
        "match": "key",
    },
    "cersai_charges": {
        "label": "Collateral charge registry (CERSAI)",
        "why": "A security interest already registered by another lender against the "
               "same asset is the classic multiple-financing fraud.",
        "feeds": ("CPT-03",),
        # Keyed by an asset identifier, so exact.
        "match": "key",
    },
}


class ReferenceList(Base):
    """One loaded version of one list."""

    __tablename__ = "reference_lists"
    __table_args__ = (
        UniqueConstraint("tenant_id", "kind", "version", name="uq_reflist_version"),
        Index("ix_reflist_active", "tenant_id", "kind", "active"),
        {"schema": ANALYTICS},
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True,
                                    default=lambda: str(uuid.uuid4()))
    tenant_id: Mapped[str] = mapped_column(String(36), index=True)
    kind: Mapped[str] = mapped_column(String(32), index=True)
    #: The publisher's own version label - "UNSC 2026-07-31", not a serial we invented.
    version: Mapped[str] = mapped_column(String(80))
    source: Mapped[str] = mapped_column(String(200), default="", server_default="")
    #: Exactly one version per (tenant, kind) is active. Enforced in the loader under a
    #: row lock rather than by constraint, because "at most one true" needs a partial
    #: unique index that would complicate the rollback path.
    active: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    effective_from: Mapped[date | None] = mapped_column(Date, nullable=True)
    entry_count: Mapped[int] = mapped_column(Integer, default=0)
    #: Of the loaded payload, so an inspection can prove which file was screened against.
    checksum: Mapped[str] = mapped_column(String(64), default="", server_default="")
    loaded_by: Mapped[str] = mapped_column(String(255))
    loaded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),
                                                server_default=func.now())
    note: Mapped[str] = mapped_column(Text, default="", server_default="")


class ReferenceEntry(Base):
    """One row of a list."""

    __tablename__ = "reference_entries"
    __table_args__ = (
        Index("ix_refentry_list_key", "list_id", "match_key"),
        Index("ix_refentry_tenant", "tenant_id", "kind"),
        {"schema": ANALYTICS},
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True,
                                    default=lambda: str(uuid.uuid4()))
    tenant_id: Mapped[str] = mapped_column(String(36), index=True)
    list_id: Mapped[str] = mapped_column(String(36), index=True)
    kind: Mapped[str] = mapped_column(String(32))
    #: What is screened against, normalised (upper, punctuation stripped). The display
    #: value is kept separately because a reviewer must see the name as published.
    match_key: Mapped[str] = mapped_column(String(300))
    display_name: Mapped[str] = mapped_column(String(300))
    #: Everything else the publisher gave: designation reference, DOB, asset details.
    attributes: Mapped[dict] = mapped_column(JSONB, default=dict)
