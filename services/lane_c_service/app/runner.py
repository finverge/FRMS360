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

from .ingestion import document_parser as dp
from .models import ComputedSignal, CreditHealthScore, FinancialStatement, LaneCAlert, ParsedFinancial
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

    signals = se.compute_all(
        cur_metrics, prior_metrics,
        cur_notes=result.notes_text, prior_notes=prior_notes,
        cur_reporting_month=statement.reporting_date.month,
        prior_reporting_month=prior_month,
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
