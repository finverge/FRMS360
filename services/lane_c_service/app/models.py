"""Data tier — Lane C: periodic borrower financial-statement assessment.

Five tables, one lifecycle: a statement arrives, gets parsed into line items, the line
items produce computed signals, the signals aggregate into a credit health score, and a
signal above threshold opens an alert for the credit team.

Keyed on ``(tenant_id, account, reporting_date)`` throughout - the same ``account``
string every CBS/LOS event already uses (``cbs_models.py``), not a separate borrower id.
Lane C's borrower already has an identity in this platform; inventing a second one would
mean two identity schemes to keep in sync for the rest of the borrower's life.
"""
import uuid
from datetime import date, datetime

from sqlalchemy import (
    BigInteger, Boolean, Date, DateTime, Float, ForeignKey, Index, Integer, String, Text,
    UniqueConstraint, func,
)
from sqlalchemy.orm import Mapped, mapped_column

from cp_common import Base
from cp_common.schemas_db import LANE_C

#: pending    - file registered, not yet parsed
#: parsed     - line items extracted, ready for signal computation
#: scored     - signals computed and a credit health score exists for this statement
#: failed     - the file could not be parsed at all (corrupt, unrecognised layout)
EXTRACTION_STATES = ("pending", "parsed", "scored", "failed")

FILING_TYPES = ("annual", "quarterly", "interim")


def _uuid() -> str:
    return str(uuid.uuid4())


class FinancialStatement(Base):
    """One submitted statement. The file itself lives on disk (``document_path``), not
    in Postgres - the same split ``IngestFile`` uses for payment-rail batch files, kept
    for the same reason: a statement PDF is tens of pages, not a small evidence
    attachment, and a blob column would make every unrelated query on this table
    slower."""

    __tablename__ = "financial_statements"
    __table_args__ = (
        UniqueConstraint("tenant_id", "account", "reporting_date", "filing_type",
                         name="uq_statement_period"),
        Index("ix_statement_tenant_account", "tenant_id", "account", "reporting_date"),
        {"schema": LANE_C},
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    tenant_id: Mapped[str] = mapped_column(String(36), index=True)
    account: Mapped[str] = mapped_column(String(40), index=True)
    reporting_date: Mapped[date] = mapped_column(Date)
    filing_type: Mapped[str] = mapped_column(String(12), default="annual")
    #: CIN/PAN, however the tenant identifies the borrower to external registries -
    #: distinct from ``account`` (this platform's own identifier) since MCA/ROC and
    #: rating feeds are keyed by the company's registry identity, not a bank-internal
    #: one. Nullable: LNC-10/LNC-11 report unmeasurable rather than guess when absent.
    company_identifier: Mapped[str | None] = mapped_column(String(24), nullable=True)
    #: Where the uploaded file was moved to, so an analyst or a re-parse can find it.
    document_path: Mapped[str] = mapped_column(String(600))
    #: Of the file's bytes - computable before parsing, so a resubmission is recognised
    #: even if extraction fails both times.
    sha256: Mapped[str] = mapped_column(String(64), index=True)
    size_bytes: Mapped[int] = mapped_column(BigInteger, default=0)
    extraction_status: Mapped[str] = mapped_column(String(12), default="pending",
                                                    index=True)
    #: Why extraction failed, when it did. Never silently dropped.
    extraction_error: Mapped[str] = mapped_column(Text, default="", server_default="")
    #: The extracted notes/auditor-report text, kept so the *next* statement's LNC-08
    #: check can compare this period's accounting-policy language against it - without
    #: this, only the fiscal-year-end-month half of LNC-08 could ever fire in the real
    #: runner, never the depreciation-method-change half.
    notes_text: Mapped[str] = mapped_column(Text, default="", server_default="")
    submitted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),
                                                    server_default=func.now())
    parsed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True),
                                                        nullable=True)


class ParsedFinancial(Base):
    """One extracted line item. ``metric_code`` is the GL-mapped canonical name
    (``REVENUE``, ``INVENTORY``, ...), never the borrower's own line-item wording -
    that mapping is what lets two borrowers' statements be compared at all.

    Carries ``tenant_id`` directly, not only via the ``statement_id`` FK - the same
    plain-column-on-every-table pattern every other fact/event table in this platform
    uses (``CbsEvent``, ``FactTransaction``, ...), so a tenant-scoped query or cleanup
    never has to join back through financial_statements to enforce isolation."""

    __tablename__ = "parsed_financials"
    __table_args__ = (
        Index("ix_parsed_statement", "statement_id", "metric_code"),
        {"schema": LANE_C},
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    tenant_id: Mapped[str] = mapped_column(String(36), index=True)
    statement_id: Mapped[str] = mapped_column(
        String(36), ForeignKey(f"{LANE_C}.financial_statements.id"), index=True)
    metric_code: Mapped[str] = mapped_column(String(32))
    #: Integer paise, the same convention as every other amount in the platform.
    metric_value_paise: Mapped[int] = mapped_column(BigInteger)
    #: 1.0 for a text-layer extraction; an OCR confidence score (0-1) otherwise, so a
    #: signal computed from a low-confidence read can be flagged as such downstream.
    confidence: Mapped[float] = mapped_column(Float, default=1.0)
    extracted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),
                                                    server_default=func.now())


class ComputedSignal(Base):
    """One (borrower, quarter, signal) result. ``peer_median`` is nullable and stays
    null - never a fabricated neutral value - when the peer cohort is too small; see
    ``benchmarking.py``'s own docstring for why that distinction matters here exactly as
    much as it does for ``cbs_features.py``'s payment-side ratios."""

    __tablename__ = "computed_signals"
    __table_args__ = (
        UniqueConstraint("tenant_id", "account", "reporting_date", "signal_code",
                         name="uq_signal_period"),
        Index("ix_signal_tenant_account", "tenant_id", "account", "reporting_date"),
        {"schema": LANE_C},
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    tenant_id: Mapped[str] = mapped_column(String(36), index=True)
    account: Mapped[str] = mapped_column(String(40), index=True)
    reporting_date: Mapped[date] = mapped_column(Date)
    signal_code: Mapped[str] = mapped_column(String(12))
    observed_value: Mapped[float] = mapped_column(Float)
    baseline_value: Mapped[float | None] = mapped_column(Float, nullable=True)
    #: Null means "no cohort of at least MIN_COHORT_SIZE peers exists", not "borrower is
    #: exactly at the median". Omitted, never defaulted - see benchmarking.py.
    peer_median: Mapped[float | None] = mapped_column(Float, nullable=True)
    #: pass | warning | critical
    status: Mapped[str] = mapped_column(String(10))
    severity: Mapped[int] = mapped_column(Integer, default=0)
    evidence: Mapped[str] = mapped_column(Text, default="", server_default="")
    #: "ratio" for the five clean financial ratios, "text-pattern" for signals read off
    #: notes/auditor-report prose rather than a structured figure - a credit analyst
    #: reads this before deciding how much to trust the number on its own.
    evidence_basis: Mapped[str] = mapped_column(String(16), default="ratio")
    computed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),
                                                   server_default=func.now())


class CreditHealthScore(Base):
    """One aggregate score per (borrower, quarter)."""

    __tablename__ = "credit_health_scores"
    __table_args__ = (
        UniqueConstraint("tenant_id", "account", "reporting_date",
                         name="uq_score_period"),
        {"schema": LANE_C},
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    tenant_id: Mapped[str] = mapped_column(String(36), index=True)
    account: Mapped[str] = mapped_column(String(40), index=True)
    reporting_date: Mapped[date] = mapped_column(Date)
    score_value: Mapped[int] = mapped_column(Integer)
    #: improving | stable | deteriorating | new (no prior quarter to compare against)
    trend: Mapped[str] = mapped_column(String(14), default="new")
    signal_count: Mapped[int] = mapped_column(Integer, default=0)
    critical_count: Mapped[int] = mapped_column(Integer, default=0)
    #: continue | monitor | investigate | escalate
    recommendation: Mapped[str] = mapped_column(String(12))
    scored_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),
                                                 server_default=func.now())


class LaneCAlert(Base):
    """A signal serious enough for the credit team to look at. Deliberately separate
    from ``ComputedSignal`` - a signal is a measurement, an alert is a work item with a
    disposition and an assignee, the same distinction Lane A/B draw between an
    observation and a ``FactAlert``."""

    __tablename__ = "alerts"
    __table_args__ = (
        Index("ix_lane_c_alert_tenant_state", "tenant_id", "status"),
        {"schema": LANE_C},
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    tenant_id: Mapped[str] = mapped_column(String(36), index=True)
    account: Mapped[str] = mapped_column(String(40), index=True)
    reporting_date: Mapped[date] = mapped_column(Date)
    signal_id: Mapped[str] = mapped_column(
        String(36), ForeignKey(f"{LANE_C}.computed_signals.id"), index=True)
    signal_code: Mapped[str] = mapped_column(String(12))
    #: critical | high | medium | low
    severity: Mapped[str] = mapped_column(String(10))
    #: new | reviewed | escalated | dismissed
    status: Mapped[str] = mapped_column(String(12), default="new", index=True)
    assigned_to: Mapped[str] = mapped_column(String(128), default="", server_default="")
    investigation_notes: Mapped[str] = mapped_column(Text, default="", server_default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),
                                                  server_default=func.now())
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True),
                                                        nullable=True)


class ManualFinding(Base):
    """A qualitative fact a credit or inspection officer records directly, for the two
    RBI signals no document or feed can ever carry: a godown inspection postponed for
    reasons that didn't hold up (LNC-15), and original bills the borrower could not
    produce for verification (LNC-16). Same "the platform can score this once a human
    enters it" shape Lane B's QUAL-01/02/03 are declared for - see
    services/config_service/app/ews_catalogue.py - except QUAL has no actual entry point
    built anywhere; this is that entry point, for Lane C's two.

    One row per (borrower, period, signal) - a fresh submission for the same period
    overwrites the prior one (see routes/lane_c.py), the same re-submission tolerance
    every other Lane C intake already gives."""

    __tablename__ = "manual_findings"
    __table_args__ = (
        UniqueConstraint("tenant_id", "account", "reporting_date", "signal_code",
                         name="uq_manual_finding_period"),
        {"schema": LANE_C},
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    tenant_id: Mapped[str] = mapped_column(String(36), index=True)
    account: Mapped[str] = mapped_column(String(40), index=True)
    reporting_date: Mapped[date] = mapped_column(Date)
    signal_code: Mapped[str] = mapped_column(String(12))
    #: True = the red flag is present (inspection was postponed / bills were not
    #: produced). False = the officer checked and found nothing wrong - a real,
    #: recorded "clean", not the same as no finding having been submitted at all.
    finding: Mapped[bool] = mapped_column(Boolean, default=False)
    notes: Mapped[str] = mapped_column(Text, default="", server_default="")
    #: The principal's own identity - who is vouching for this fact matters as much as
    #: the fact itself for a manually-entered signal.
    submitted_by: Mapped[str] = mapped_column(String(255))
    submitted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),
                                                    server_default=func.now())


class ProjectAppraisal(Base):
    """The sanctioned baseline for a project-finance loan - cost and completion date as
    appraised at sanction. One per borrower, set once (and updated only if the sanction
    itself is formally revised), unlike ProjectProgress below which is periodic. Neither
    LNC-02 nor LNC-22 can measure anything without this: a variance needs two points,
    and this is the fixed one."""

    __tablename__ = "project_appraisals"
    __table_args__ = (
        UniqueConstraint("tenant_id", "account", name="uq_project_appraisal_account"),
        {"schema": LANE_C},
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    tenant_id: Mapped[str] = mapped_column(String(36), index=True)
    account: Mapped[str] = mapped_column(String(40), index=True)
    sanctioned_cost_paise: Mapped[int] = mapped_column(BigInteger)
    sanctioned_completion_date: Mapped[date] = mapped_column(Date)
    submitted_by: Mapped[str] = mapped_column(String(255))
    submitted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),
                                                    server_default=func.now())


class ProjectProgress(Base):
    """A periodic update against the appraised baseline above: cost incurred so far, and
    a revised completion date if the project's timeline has moved. One per (borrower,
    review period) - a fresh submission for the same period overwrites the prior one,
    the same re-submission tolerance ManualFinding already gives."""

    __tablename__ = "project_progress"
    __table_args__ = (
        UniqueConstraint("tenant_id", "account", "reporting_date",
                         name="uq_project_progress_period"),
        {"schema": LANE_C},
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    tenant_id: Mapped[str] = mapped_column(String(36), index=True)
    account: Mapped[str] = mapped_column(String(40), index=True)
    reporting_date: Mapped[date] = mapped_column(Date)
    actual_cost_incurred_paise: Mapped[int] = mapped_column(BigInteger)
    #: Null means "no revision reported this period", not "revised to the same date" -
    #: the same absent-vs-zero honesty every other nullable fact here keeps.
    revised_completion_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    notes: Mapped[str] = mapped_column(Text, default="", server_default="")
    submitted_by: Mapped[str] = mapped_column(String(255))
    submitted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),
                                                    server_default=func.now())
