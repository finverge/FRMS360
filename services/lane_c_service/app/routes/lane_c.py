"""Presentation tier - statement intake, and what the credit team reads back.

Upload registers a pending statement and returns immediately; parsing, signal
computation and scoring happen in the batch runner (scripts/run_lane_c.py), not
synchronously in the request - the same split ingestion-service's file intake and the
detection batch worker already have, for the same reason: a multi-page PDF's OCR fallback
can take real wall-clock time, which does not belong inside an HTTP request/response
cycle a caller is waiting on.
"""
import hashlib
from datetime import date
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, UploadFile
from sqlalchemy import select
from sqlalchemy.orm import Session

from cp_common import (
    AppError, Principal, get_current_principal, get_session, resolve_tenant_scope,
    settings,
)

from ..entity_gate import can_extend_credit
from ..models import FILING_TYPES, ComputedSignal, CreditHealthScore, FinancialStatement, LaneCAlert
from ..signal_catalogue import signal_definitions

router = APIRouter(prefix="/lane-c", tags=["lane-c"])

#: A statement large enough to need this is almost certainly the wrong file - the same
#: reasoning CaseDocument's MAX_BYTES applies, sized up for a multi-page annual report.
MAX_BYTES = 25 * 1024 * 1024


@router.post("/{tenant_id}/ingest", status_code=202)
async def ingest_statement(
    tenant_id: str,
    account: str = Form(...),
    reporting_date: date = Form(...),
    filing_type: str = Form("annual"),
    file: UploadFile = File(...),
    db: Session = Depends(get_session),
    principal: Principal = Depends(get_current_principal),
) -> dict:
    resolve_tenant_scope(principal, tenant_id)
    eligible, reason = can_extend_credit(tenant_id)
    if not eligible:
        raise AppError(f"Lane C is not available for this tenant: {reason}",
                       409, "entity_not_credit_eligible")
    if filing_type not in FILING_TYPES:
        raise AppError(f"filing_type must be one of {', '.join(FILING_TYPES)}",
                       422, "invalid_filing_type")

    data = await file.read()
    if len(data) > MAX_BYTES:
        raise AppError(f"File exceeds {MAX_BYTES // (1024 * 1024)}MB",
                       413, "file_too_large")
    digest = hashlib.sha256(data).hexdigest()

    storage_dir = Path(settings.lane_c_storage_dir) / tenant_id
    storage_dir.mkdir(parents=True, exist_ok=True)
    suffix = Path(file.filename or "statement.pdf").suffix or ".pdf"
    document_path = storage_dir / f"{digest}{suffix}"
    document_path.write_bytes(data)

    existing = db.scalars(select(FinancialStatement).where(
        FinancialStatement.tenant_id == tenant_id, FinancialStatement.account == account,
        FinancialStatement.reporting_date == reporting_date,
        FinancialStatement.filing_type == filing_type)).first()
    if existing is not None:
        # A resubmission for the same period - overwrite the file on disk (already done
        # above, same sha256-named path if byte-identical) and reset for reprocessing
        # rather than reject outright, the same re-delivery tolerance file_model.py's
        # batch intake gives a byte-identical redelivered file.
        existing.document_path = str(document_path)
        existing.sha256 = digest
        existing.size_bytes = len(data)
        existing.extraction_status = "pending"
        existing.extraction_error = ""
        db.commit()
        return {"statement_id": existing.id, "extraction_status": "pending",
                "resubmission": True}

    stmt = FinancialStatement(
        tenant_id=tenant_id, account=account, reporting_date=reporting_date,
        filing_type=filing_type, document_path=str(document_path), sha256=digest,
        size_bytes=len(data),
    )
    db.add(stmt)
    db.commit()
    return {"statement_id": stmt.id, "extraction_status": "pending", "resubmission": False}


@router.get("/{tenant_id}/borrowers/{account}/score")
def latest_score(
    tenant_id: str, account: str,
    db: Session = Depends(get_session),
    principal: Principal = Depends(get_current_principal),
) -> dict:
    resolve_tenant_scope(principal, tenant_id)
    row = db.scalars(select(CreditHealthScore).where(
        CreditHealthScore.tenant_id == tenant_id, CreditHealthScore.account == account
    ).order_by(CreditHealthScore.reporting_date.desc()).limit(1)).first()
    if row is None:
        return {"account": account, "available": False}
    return {
        "account": account, "available": True, "reporting_date": row.reporting_date.isoformat(),
        "score_value": row.score_value, "trend": row.trend, "signal_count": row.signal_count,
        "critical_count": row.critical_count, "recommendation": row.recommendation,
    }


@router.get("/{tenant_id}/borrowers/{account}/signals")
def signals_for_period(
    tenant_id: str, account: str, reporting_date: date,
    db: Session = Depends(get_session),
    principal: Principal = Depends(get_current_principal),
) -> dict:
    resolve_tenant_scope(principal, tenant_id)
    catalogue = signal_definitions()
    rows = db.scalars(select(ComputedSignal).where(
        ComputedSignal.tenant_id == tenant_id, ComputedSignal.account == account,
        ComputedSignal.reporting_date == reporting_date)).all()
    return {
        "account": account, "reporting_date": reporting_date.isoformat(),
        "signals": [{
            "signal_code": r.signal_code, "name": catalogue.get(r.signal_code, {}).get("name", ""),
            "observed_value": r.observed_value, "baseline_value": r.baseline_value,
            "peer_median": r.peer_median, "status": r.status, "severity": r.severity,
            "evidence": r.evidence, "evidence_basis": r.evidence_basis,
        } for r in rows],
    }


@router.get("/{tenant_id}/alerts")
def list_alerts(
    tenant_id: str, status: str | None = None,
    db: Session = Depends(get_session),
    principal: Principal = Depends(get_current_principal),
) -> dict:
    resolve_tenant_scope(principal, tenant_id)
    q = select(LaneCAlert).where(LaneCAlert.tenant_id == tenant_id)
    if status:
        q = q.where(LaneCAlert.status == status)
    rows = db.scalars(q.order_by(LaneCAlert.created_at.desc())).all()
    return {"alerts": [{
        "alert_id": r.id, "account": r.account, "reporting_date": r.reporting_date.isoformat(),
        "signal_code": r.signal_code, "severity": r.severity, "status": r.status,
        "assigned_to": r.assigned_to, "created_at": r.created_at.isoformat(),
    } for r in rows]}


@router.post("/{tenant_id}/alerts/{alert_id}/review")
def review_alert(
    tenant_id: str, alert_id: str,
    status: str = Form(...), notes: str = Form(""), assigned_to: str = Form(""),
    db: Session = Depends(get_session),
    principal: Principal = Depends(get_current_principal),
) -> dict:
    resolve_tenant_scope(principal, tenant_id)
    if status not in ("reviewed", "escalated", "dismissed"):
        raise AppError("status must be one of reviewed, escalated, dismissed",
                       422, "invalid_status")
    alert = db.scalars(select(LaneCAlert).where(
        LaneCAlert.id == alert_id, LaneCAlert.tenant_id == tenant_id)).first()
    if alert is None:
        raise AppError("Alert not found", 404, "alert_not_found")
    from datetime import datetime, timezone
    alert.status = status
    alert.investigation_notes = notes
    if assigned_to:
        alert.assigned_to = assigned_to
    if status in ("dismissed",):
        alert.closed_at = datetime.now(timezone.utc)
    db.commit()
    return {"alert_id": alert.id, "status": alert.status}
