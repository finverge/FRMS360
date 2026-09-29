"""Analytical fact tables.

Three grains, one strict hierarchy:  transaction -> alert -> case.
Every dashboard figure is derived from these and nothing else, which is what makes
the reconciliation invariants in ``reconciliation.py`` provable rather than aspirational.

Money is stored as **integer paise**, never float. Floating-point money does not add up,
and a reconciliation suite built on floats will produce false breaks forever.

In production these live in the per-tenant data-plane cell (and later a columnar engine);
here they sit in the same PostgreSQL instance, logically scoped by tenant_id.
"""
from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, Float, Index, String
from sqlalchemy.orm import Mapped, mapped_column

from cp_common import Base
from cp_common.schemas_db import ANALYTICS

# ---- controlled vocabularies (kept here so the generator and the metric registry agree) ----
RAILS = ["UPI", "IMPS", "NEFT", "RTGS", "CARD"]
EWS_FAMILIES = ["VEL", "SME", "BEH", "LAY", "CPT", "CHN", "TBM", "CBS", "QUAL"]
SEVERITIES = ["critical", "high", "medium", "low"]
DISPOSITIONS = ["pending", "true_positive", "false_positive"]
CASE_STATES = [
    "under_review", "rfa_flagged", "natural_justice", "response_evaluation",
    "fraud_declared", "fmr_reported", "closed_fraud", "exonerated",
]
# States that count toward the board's headline fraud figure.
FRAUD_STATES = ["fraud_declared", "fmr_reported", "closed_fraud"]
# RBI Fraud Monitoring Return categories (illustrative labels).
FMR_CATEGORIES = [
    "misappropriation_breach_of_trust", "fraudulent_encashment", "manipulation_of_books",
    "unauthorised_credit_facility", "cash_shortage", "cheating_and_forgery",
    "forex_transactions", "advances_related", "others",
]
PRODUCTS = ["savings", "current", "loan", "credit_card", "trade_finance"]
SEGMENTS = ["retail", "msme", "corporate", "hni", "nri"]
REGIONS = ["north", "south", "east", "west", "central"]


class FactTransaction(Base):
    __tablename__ = "fact_transaction"

    txn_id: Mapped[str] = mapped_column(String(40), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(36), index=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    rail: Mapped[str] = mapped_column(String(8), index=True)
    amount_paise: Mapped[int] = mapped_column(BigInteger)
    debtor_account: Mapped[str] = mapped_column(String(32), index=True)
    creditor_account: Mapped[str] = mapped_column(String(32), index=True)
    branch: Mapped[str] = mapped_column(String(16))
    region: Mapped[str] = mapped_column(String(16), index=True)
    product: Mapped[str] = mapped_column(String(20), index=True)
    customer_segment: Mapped[str] = mapped_column(String(12), index=True)
    channel: Mapped[str] = mapped_column(String(16))
    device_id: Mapped[str] = mapped_column(String(32))
    ip_addr: Mapped[str] = mapped_column(String(45))
    status: Mapped[str] = mapped_column(String(12))  # settled | interdicted | rejected
    # 0.0-1.0 from an integrated device-fingerprinting / behavioral-biometrics provider
    # (CHN-04/CHN-05 - see detection/features.py's NEEDS_EXTERNAL_DATA). Absent (NULL) is
    # the normal case until a tenant has that integration wired up - never defaulted to
    # 0.0, which would read as "checked, and clean" rather than "never checked".
    device_risk_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    behavior_anomaly_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    # Where this row came from. The existing demo corpus keeps the default, so live
    # traffic is distinguishable from it without deleting anything - the two can sit
    # side by side and a dashboard can be filtered to either.
    source: Mapped[str] = mapped_column(String(16), default="live",
                                        server_default="synthetic", index=True)

    __table_args__ = (Index("ix_txn_tenant_ts", "tenant_id", "ts"),
                      {"schema": ANALYTICS})


class FactAlert(Base):
    __tablename__ = "fact_alert"

    alert_id: Mapped[str] = mapped_column(String(40), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(36), index=True)
    txn_id: Mapped[str] = mapped_column(String(40), index=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    rule_family: Mapped[str] = mapped_column(String(8), index=True)
    rule_id: Mapped[str] = mapped_column(String(24), index=True)
    typology: Mapped[str] = mapped_column(String(48))
    score: Mapped[float] = mapped_column(Float)
    severity: Mapped[str] = mapped_column(String(10), index=True)
    disposition: Mapped[str] = mapped_column(String(16), index=True)
    case_id: Mapped[str | None] = mapped_column(String(40), nullable=True, index=True)
    analyst: Mapped[str] = mapped_column(String(64), default="")
    first_touch_ts: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    disposition_ts: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Config version that produced this score - pinned so a reopened case is explained
    # with the rules that actually ran, not today's rules.
    config_version: Mapped[str] = mapped_column(String(16), default="1.0.0")

    # ---- rule-evaluation trace ----
    # A score alone cannot answer "why was this flagged". These record which band of the
    # rule matched and on what observation, so the decision can be reconstructed years
    # later against the config version above.
    sub_rule_ref: Mapped[str] = mapped_column(String(8), default="", server_default="")      # e.g. ".03"
    matched_reason: Mapped[str] = mapped_column(String(255), default="", server_default="")  # the band's reason text
    observed_value: Mapped[float | None] = mapped_column(Float, nullable=True)
    threshold_value: Mapped[float | None] = mapped_column(Float, nullable=True)
    observed_unit: Mapped[str] = mapped_column(String(24), default="", server_default="")

    # Where this row came from. The existing demo corpus keeps the default, so live
    # traffic is distinguishable from it without deleting anything - the two can sit
    # side by side and a dashboard can be filtered to either.
    source: Mapped[str] = mapped_column(String(16), default="live",
                                        server_default="synthetic", index=True)

    __table_args__ = (Index("ix_alert_tenant_ts", "tenant_id", "ts"),
                      {"schema": ANALYTICS})


class FactCase(Base):
    __tablename__ = "fact_case"

    case_id: Mapped[str] = mapped_column(String(40), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(36), index=True)
    opened_ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    state: Mapped[str] = mapped_column(String(24), index=True)
    severity: Mapped[str] = mapped_column(String(10), index=True)
    fmr_category: Mapped[str] = mapped_column(String(40), index=True)
    # Derived: must equal the sum of linked alerts' transaction amounts (invariant R-03).
    amount_paise: Mapped[int] = mapped_column(BigInteger, default=0)
    recovered_paise: Mapped[int] = mapped_column(BigInteger, default=0)
    rfa_flag: Mapped[bool] = mapped_column(Boolean, default=False)
    # Provenance, inherited from the alerts that opened the case. A case is only as live
    # as its evidence, and it is the case - not the alert - that a return is filed against.
    source: Mapped[str] = mapped_column(String(16), default="live",
                                        server_default="synthetic", index=True)
    rail: Mapped[str] = mapped_column(String(8), index=True)
    region: Mapped[str] = mapped_column(String(16), index=True)
    product: Mapped[str] = mapped_column(String(20), index=True)
    customer_segment: Mapped[str] = mapped_column(String(12), index=True)
    assignee: Mapped[str] = mapped_column(String(64), default="")
    # Natural-justice + regulatory clocks
    show_cause_ts: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    response_due_ts: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    decision_ts: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    fmr_due_ts: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    fmr_filed_ts: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    str_due_ts: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    str_filed_ts: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # BR-414. Runs from the fraud declaration, on a much longer clock than the returns.
    accountability_due_ts: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True)
    closed_ts: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (Index("ix_case_tenant_opened", "tenant_id", "opened_ts"),
                      {"schema": ANALYTICS})


class AccountRingScore(Base):
    """AI/ML roadmap Phase 3 (HLD AD-15) - the boundary between "expensive, computed on
    a schedule" and "cheap, read per transaction".

    Written only by ``scripts/refresh_ring_scores.py``, a periodic job (the same shape
    as ``run_subscriptions.py``/``run_retention.py``), never by the live detection
    worker: a GraphSAGE forward pass over a tenant's whole account graph does not fit
    the per-batch budget the way VEL-04's five-feature IsolationForest call does.
    engine.py's LAY-05 lookup is a plain primary-key read against this table, the same
    shape ``device_clusters()``'s per-batch dict-return already has for LAY-04 - never
    a live model call in the payment path.

    One row per account, not one per computation - a refresh replaces the previous
    score rather than appending a history, since detection only ever needs the latest.
    """
    __tablename__ = "account_ring_score"

    tenant_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    account: Mapped[str] = mapped_column(String(32), primary_key=True)
    score: Mapped[float] = mapped_column(Float)
    # Which model config version produced this score - pinned so a LAY-05 alert is
    # explained by the model that actually scored it, the same discipline FactAlert's
    # own config_version column already has for rule-based alerts.
    model_version: Mapped[str] = mapped_column(String(16))
    computed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)

    __table_args__ = ({"schema": ANALYTICS},)
