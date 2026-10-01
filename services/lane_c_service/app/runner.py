"""The Lane C batch runner: claims pending statements, parses them, computes signals,
scores the borrower, and opens alerts for anything a credit analyst should see.

Same claiming discipline every other batch path in this platform uses (``evaluate_los``,
``run_detection.py``): ``FOR UPDATE SKIP LOCKED`` so several copies can run against the
same queue without duplicating work, never an application-level lock.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import reference_fetch as rf
from .ingestion import document_parser as dp
from .models import (
    ComputedSignal, CreditHealthScore, FinancialStatement, LaneCAlert, ManualFinding,
    ParsedFinancial, ProjectAppraisal, ProjectProgress,
)
from .scoring import credit_health_scorer as scorer
from .signals import signal_engine as se

log = logging.getLogger("lane_c")

#: A fired signal at this band or above opens a work item for the credit team - "low"
#: is recorded (on the signal) but does not by itself demand review.
ALERT_BANDS = {"critical", "high", "medium"}


@dataclass
class RunReport:
    claimed: int = 0
    parsed: int = 0
    failed: int = 0
    scored: int = 0
    alerts: int = 0
    fired_signals: dict[str, int] = field(default_factory=dict)


def _prior_period(db: Session, tenant_id: str, account: str, before: date
                  ) -> tuple[dict[str, int] | None, str | None, int | None]:
    """The most recent statement strictly before ``before`` for this borrower: its
    metric_code -> paise dict, its stored notes text, and its reporting month - or
    (None, None, None) for a borrower's first-ever submission."""
    prior_stmt = db.scalars(
        select(FinancialStatement)
        .where(FinancialStatement.tenant_id == tenant_id,
               FinancialStatement.account == account,
               FinancialStatement.reporting_date < before,
               FinancialStatement.extraction_status == "scored")
        .order_by(FinancialStatement.reporting_date.desc())
        .limit(1)
    ).first()
    if prior_stmt is None:
        return None, None, None
    rows = db.scalars(
        select(ParsedFinancial).where(ParsedFinancial.statement_id == prior_stmt.id)
    ).all()
    metrics = {r.metric_code: r.metric_value_paise for r in rows}
    return metrics, prior_stmt.notes_text, prior_stmt.reporting_date.month


def _prior_rank(db: Session, tenant_id: str, account: str, before: date) -> float | None:
    """The rank LNC-11 recorded the last time this borrower was reviewed, so a downgrade
    can be detected without a separate rating-history table - ComputedSignal already
    persists observed_value per period, the same storage every other signal uses."""
    prior = db.scalars(
        select(ComputedSignal)
        .where(ComputedSignal.tenant_id == tenant_id, ComputedSignal.account == account,
               ComputedSignal.reporting_date < before, ComputedSignal.signal_code == "LNC-11")
        .order_by(ComputedSignal.reporting_date.desc())
        .limit(1)
    ).first()
    return prior.observed_value if prior else None


def _manual_finding(db: Session, tenant_id: str, account: str, reporting_date: date,
                    signal_code: str) -> dict | None:
    """A human's own recorded fact for exactly this (borrower, period, signal) - not
    "as of" or "most recent", an exact match, since a godown-inspection finding is tied
    to one specific review cycle, not carried forward the way a rating stays active
    until superseded."""
    row = db.scalars(select(ManualFinding).where(
        ManualFinding.tenant_id == tenant_id, ManualFinding.account == account,
        ManualFinding.reporting_date == reporting_date,
        ManualFinding.signal_code == signal_code)).first()
    if row is None:
        return None
    return {"finding": row.finding, "notes": row.notes}


def _prior_promoter_pct(db: Session, tenant_id: str, account: str, before: date
                        ) -> float | None:
    """Same storage reuse as _prior_rank(): LNC-19's own observed_value from the most
    recent prior period it was actually measured, no separate shareholding-history table."""
    prior = db.scalars(
        select(ComputedSignal)
        .where(ComputedSignal.tenant_id == tenant_id, ComputedSignal.account == account,
               ComputedSignal.reporting_date < before, ComputedSignal.signal_code == "LNC-19")
        .order_by(ComputedSignal.reporting_date.desc())
        .limit(1)
    ).first()
    return prior.observed_value if prior else None


def _project_baseline(db: Session, tenant_id: str, account: str) -> dict | None:
    """The sanctioned project-appraisal baseline for this borrower, if one was ever
    submitted - a single standing fact, not tied to any one review period, unlike
    ProjectProgress below."""
    row = db.scalars(select(ProjectAppraisal).where(
        ProjectAppraisal.tenant_id == tenant_id, ProjectAppraisal.account == account)
        ).first()
    if row is None:
        return None
    return {"sanctioned_cost_paise": row.sanctioned_cost_paise,
            "sanctioned_completion_date": row.sanctioned_completion_date}


def _project_progress(db: Session, tenant_id: str, account: str, reporting_date: date
                      ) -> dict | None:
    """This exact period's progress submission - same exact-match shape as
    _manual_finding(), since a cost-incurred figure is tied to one specific review
    cycle, not carried forward."""
    row = db.scalars(select(ProjectProgress).where(
        ProjectProgress.tenant_id == tenant_id, ProjectProgress.account == account,
        ProjectProgress.reporting_date == reporting_date)).first()
    if row is None:
        return None
    return {"actual_cost_incurred_paise": row.actual_cost_incurred_paise,
            "revised_completion_date": row.revised_completion_date}


def _prior_score(db: Session, tenant_id: str, account: str, before: date) -> int | None:
    prior = db.scalars(
        select(CreditHealthScore)
        .where(CreditHealthScore.tenant_id == tenant_id,
               CreditHealthScore.account == account,
               CreditHealthScore.reporting_date < before)
        .order_by(CreditHealthScore.reporting_date.desc())
        .limit(1)
    ).first()
    return prior.score_value if prior else None


def process_statement(db: Session, statement: FinancialStatement, rep: RunReport) -> None:
    result = dp.extract(statement.document_path)
    if not result.ok:
        statement.extraction_status = "failed"
        statement.extraction_error = result.error
        rep.failed += 1
        return

    for m in result.metrics:
        db.add(ParsedFinancial(tenant_id=statement.tenant_id, statement_id=statement.id,
                               metric_code=m.metric_code, metric_value_paise=m.value_paise,
                               confidence=m.confidence))
    statement.notes_text = result.notes_text
    statement.extraction_status = "parsed"
    rep.parsed += 1

    cur_metrics = {m.metric_code: m.value_paise for m in result.metrics}
    prior_metrics, prior_notes, prior_month = _prior_period(
        db, statement.tenant_id, statement.account, statement.reporting_date)

    # LNC-10/LNC-11's reference-feed lookups, pre-fetched here rather than inside the
    # signal functions - same "runner does the I/O, signal_engine.py stays pure" split
    # prior_metrics/prior_notes already follow.
    company_id = statement.company_identifier
    roc_entry = rf.active_entry(db, statement.tenant_id, "mca_roc", company_id) \
        if company_id else None
    rating_entry = rf.active_entry(db, statement.tenant_id, "rating_action", company_id) \
        if company_id else None
    prior_rank = _prior_rank(db, statement.tenant_id, statement.account,
                             statement.reporting_date)
    godown_entry = _manual_finding(db, statement.tenant_id, statement.account,
                                   statement.reporting_date, "LNC-15")
    bills_entry = _manual_finding(db, statement.tenant_id, statement.account,
                                  statement.reporting_date, "LNC-16")
    insurance_entry = rf.active_entry(db, statement.tenant_id, "insurance_coverage", company_id) \
        if company_id else None
    stock_audit_entry = rf.active_entry(db, statement.tenant_id, "stock_audit", company_id) \
        if company_id else None
    shareholding_entry = rf.active_entry(db, statement.tenant_id, "shareholding", company_id) \
        if company_id else None
    prior_promoter_pct = _prior_promoter_pct(db, statement.tenant_id, statement.account,
                                             statement.reporting_date)
    enforcement_entry = rf.active_entry(db, statement.tenant_id, "enforcement_action", company_id) \
        if company_id else None
    enforcement_feed_loaded = rf.is_loaded(db, statement.tenant_id, "enforcement_action")
    invoice_entry = _manual_finding(db, statement.tenant_id, statement.account,
                                    statement.reporting_date, "LNC-21")
    management_change_entry = rf.active_entry(db, statement.tenant_id, "management_changes",
                                              company_id) if company_id else None
    project_baseline = _project_baseline(db, statement.tenant_id, statement.account)
    project_progress = _project_progress(db, statement.tenant_id, statement.account,
                                         statement.reporting_date)

    signals = se.compute_all(
        cur_metrics, prior_metrics,
        cur_notes=result.notes_text, prior_notes=prior_notes,
        cur_reporting_month=statement.reporting_date.month,
        prior_reporting_month=prior_month,
        company_identifier=company_id, roc_entry=roc_entry,
        rating_entry=rating_entry, prior_rank=prior_rank,
        godown_entry=godown_entry, bills_entry=bills_entry,
        insurance_entry=insurance_entry, stock_audit_entry=stock_audit_entry,
        shareholding_entry=shareholding_entry, prior_promoter_pct=prior_promoter_pct,
        enforcement_entry=enforcement_entry, enforcement_feed_loaded=enforcement_feed_loaded,
        invoice_entry=invoice_entry, management_change_entry=management_change_entry,
        project_baseline=project_baseline, project_progress=project_progress,
    )

    signal_rows: dict[str, ComputedSignal] = {}
    for code, r in signals.items():
        if r.status == "unmeasurable":
            continue
        row = ComputedSignal(
            tenant_id=statement.tenant_id, account=statement.account,
            reporting_date=statement.reporting_date, signal_code=code,
            observed_value=r.observed_value if r.observed_value is not None else 0.0,
            baseline_value=r.baseline_value, status=r.status, severity=r.severity,
            evidence=r.evidence, evidence_basis=r.evidence_basis,
        )
        db.add(row)
        signal_rows[code] = row
        if r.status != "pass":
            rep.fired_signals[code] = rep.fired_signals.get(code, 0) + 1
    db.flush()  # assign ids before alerts reference them

    prior_score_value = _prior_score(db, statement.tenant_id, statement.account,
                                     statement.reporting_date)
    score_result = scorer.score(signals, prior_score_value)
    db.add(CreditHealthScore(
        tenant_id=statement.tenant_id, account=statement.account,
        reporting_date=statement.reporting_date, score_value=score_result.score_value,
        trend=score_result.trend, signal_count=score_result.signal_count,
        critical_count=score_result.critical_count,
        recommendation=score_result.recommendation,
    ))
    rep.scored += 1

    for code, r in signals.items():
        if r.status in ALERT_BANDS and code in signal_rows:
            db.add(LaneCAlert(
                tenant_id=statement.tenant_id, account=statement.account,
                reporting_date=statement.reporting_date,
                signal_id=signal_rows[code].id, signal_code=code,
                severity=r.status,
            ))
            rep.alerts += 1

    statement.extraction_status = "scored"


def run_once(db: Session, tenant_id: str, *, limit: int = 100) -> RunReport:
    """Claim and process one batch of pending statements for a tenant."""
    rep = RunReport()
    statements = db.scalars(
        select(FinancialStatement)
        .where(FinancialStatement.tenant_id == tenant_id,
               FinancialStatement.extraction_status == "pending")
        .order_by(FinancialStatement.submitted_at)
        .limit(limit)
        .with_for_update(skip_locked=True)
    ).all()
    rep.claimed = len(statements)
    for statement in statements:
        process_statement(db, statement, rep)
    db.commit()
    return rep
