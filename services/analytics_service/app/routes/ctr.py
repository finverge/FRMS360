"""Cash Transaction Report endpoints (BR-508).

Same division of labour as the FMR/STR routes: **the platform aggregates and prepares, a
person submits, and the platform records what came back.** Nothing here transmits
anything to FIU-IND.

Account-and-month keyed rather than case-keyed - see ``ctr.py``'s module docstring for
why this is not just another ``kind`` on the FMR/STR filing routes.
"""
import hashlib
import json
from datetime import datetime, timezone

import httpx
from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from cp_common import (
    AppError, Principal, get_current_principal, get_session, record_audit,
    resolve_tenant_scope, settings,
)
from cp_common.dynamic_roles import has_permission

from .. import ctr, ctr_renderers, data_source
from ..ctr_model import CtrFiling
from ..rules import policy

router = APIRouter(prefix="/analytics", tags=["ctr"])

# Who may do this is data: the tenant's own role rows grant "ctr.file"
# (cp_common.permissions); nothing here names a role.
PERMISSION = "ctr.file"


def _may_file(tenant_id: str, principal: Principal) -> bool:
    return has_permission(tenant_id, principal.role, PERMISSION)


def _entity(tenant_id: str) -> dict:
    pol = policy(tenant_id)
    out = {"entity_type": pol.get("entity_type"), "entity_label": pol.get("entity_label")}
    try:
        r = httpx.get(f"{settings.tenant_service_url}/tenants/internal/{tenant_id}/identity",
                      headers={"x-internal-key": settings.internal_api_key}, timeout=5.0)
        r.raise_for_status()
        t = r.json()
        out.update({"legal_name": t.get("legal_name"), "display_name": t.get("display_name")})
    except Exception:  # noqa: BLE001
        pass
    return out


def _hash(payload: dict) -> str:
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()


@router.get("/{tenant_id}/ctr/due")
def due(
    tenant_id: str,
    period: str = Query(..., description="Calendar month, YYYY-MM"),
    db: Session = Depends(get_session),
    principal: Principal = Depends(get_current_principal),
) -> dict:
    """Every account whose cash transactions this month meet the Rs 10,00,000
    threshold - the working list, before anything is generated."""
    resolve_tenant_scope(principal, tenant_id)
    try:
        rows = ctr.due_accounts(db, tenant_id, period)
    except ValueError as exc:
        raise AppError(str(exc), 400, "bad_period")
    return {"period": period, "count": len(rows), "items": [{
        "account": r["account"], "transaction_count": int(r["txn_count"]),
        "total_amount_paise": int(r["total_paise"] or 0),
    } for r in rows]}


@router.get("/{tenant_id}/ctr/{account}/{period}/readiness")
def readiness(
    tenant_id: str, account: str, period: str,
    db: Session = Depends(get_session),
    principal: Principal = Depends(get_current_principal),
) -> dict:
    """Can this account's CTR for this month be filed, and if not, what is missing?"""
    resolve_tenant_scope(principal, tenant_id)
    try:
        payload = ctr.build(db, tenant_id=tenant_id, account=account, period=period,
                            entity=_entity(tenant_id))
    except LookupError:
        raise AppError("No cash transactions for this account in this period", 404,
                       "not_found")
    except data_source.NotLiveData as exc:
        raise AppError(str(exc), 422, "not_live_data")
    except ValueError as exc:
        raise AppError(str(exc), 400, "bad_period")
    return {**ctr.validate(payload).as_dict(), "schema_version": ctr.SCHEMA_VERSION,
            "binding": ctr.FORMAT_BINDING}


@router.post("/{tenant_id}/ctr/{account}/{period}/generate")
def generate(
    tenant_id: str, account: str, period: str,
    db: Session = Depends(get_session),
    principal: Principal = Depends(get_current_principal),
) -> dict:
    """Build and store a CTR. Refuses below the threshold or with an unevidenced field -
    same discipline as FMR/STR generation."""
    resolve_tenant_scope(principal, tenant_id)
    if not _may_file(tenant_id, principal):
        raise AppError("Your role may not prepare regulatory returns", 403,
                       "role_not_permitted")

    try:
        payload = ctr.build(db, tenant_id=tenant_id, account=account, period=period,
                            entity=_entity(tenant_id))
    except LookupError:
        raise AppError("No cash transactions for this account in this period", 404,
                       "not_found")
    except data_source.NotLiveData as exc:
        raise AppError(str(exc), 422, "not_live_data")
    except ValueError as exc:
        raise AppError(str(exc), 400, "bad_period")

    validation = ctr.validate(payload)
    if not validation.ready:
        raise AppError(
            "This account cannot be filed for this period yet: " + ", ".join(
                g.label for g in validation.blocking),
            409, "not_ready_to_file")

    prior = db.scalars(select(CtrFiling).where(
        CtrFiling.tenant_id == tenant_id, CtrFiling.account == account,
        CtrFiling.period == period)).all()
    # An earlier draft that was never submitted is superseded; an acknowledged one is
    # not touched, because it is a record of what actually went to FIU-IND.
    for p in prior:
        if p.status == "generated":
            p.status = "superseded"
    revision = len(prior) + 1

    row = CtrFiling(
        tenant_id=tenant_id, account=account, period=period,
        schema_version=ctr.SCHEMA_VERSION, payload=payload, content_hash=_hash(payload),
        revision=revision, generated_by=principal.subject)
    db.add(row)
    db.commit()

    record_audit(
        service="analytics-service", action="ctr.generated", actor=principal.subject,
        actor_role=principal.role, tenant_id=tenant_id, target_type="ctr_filing",
        target_id=row.id, status="success",
        detail={"account": account, "period": period, "revision": revision,
                "hash": row.content_hash[:16]})

    return {"id": row.id, "account": account, "period": period, "revision": revision,
            "content_hash": row.content_hash, "status": row.status,
            "validation": validation.as_dict(), "generated_at": row.generated_at,
            "binding": ctr.FORMAT_BINDING}


@router.get("/{tenant_id}/ctr/{account}/{period}")
def list_filings(
    tenant_id: str, account: str, period: str,
    db: Session = Depends(get_session),
    principal: Principal = Depends(get_current_principal),
) -> list[dict]:
    resolve_tenant_scope(principal, tenant_id)
    rows = db.scalars(select(CtrFiling).where(
        CtrFiling.tenant_id == tenant_id, CtrFiling.account == account,
        CtrFiling.period == period).order_by(CtrFiling.generated_at.desc())).all()
    return [{"id": r.id, "account": r.account, "period": r.period,
             "revision": r.revision, "status": r.status,
             "schema_version": r.schema_version, "content_hash": r.content_hash,
             "generated_at": r.generated_at, "generated_by": r.generated_by,
             "submitted_at": r.submitted_at, "submitted_by": r.submitted_by,
             "reference_number": r.reference_number} for r in rows]


@router.get("/{tenant_id}/ctr-filings/{filing_id}/download")
def download(
    tenant_id: str, filing_id: str,
    fmt: str = Query(default="html"),
    db: Session = Depends(get_session),
    principal: Principal = Depends(get_current_principal),
) -> Response:
    resolve_tenant_scope(principal, tenant_id)
    if not _may_file(tenant_id, principal):
        raise AppError("Your role may not download regulatory returns", 403,
                       "role_not_permitted")
    if fmt not in ctr_renderers.RENDERERS:
        raise AppError(f"Unsupported format '{fmt}'", 400, "unknown_format")

    row = db.get(CtrFiling, filing_id)
    if row is None or row.tenant_id != tenant_id:
        raise AppError("Filing not found", 404, "not_found")

    validation = ctr.validate(row.payload).as_dict()
    body = ctr_renderers.RENDERERS[fmt](row.payload, validation)
    record_audit(
        service="analytics-service", action="ctr.downloaded", actor=principal.subject,
        actor_role=principal.role, tenant_id=tenant_id, target_type="ctr_filing",
        target_id=filing_id, status="success",
        detail={"format": fmt, "account": row.account, "period": row.period})

    name = f"CTR-{row.account}-{row.period}-r{row.revision}.{fmt}"
    return Response(content=body, media_type=ctr_renderers.MEDIA_TYPES[fmt],
                    headers={"Content-Disposition": f'inline; filename="{name}"'})


class Acknowledge(BaseModel):
    reference_number: str = Field(min_length=1, max_length=120)
    note: str = Field(default="", max_length=2000)


@router.post("/{tenant_id}/ctr-filings/{filing_id}/acknowledge")
def acknowledge(
    tenant_id: str, filing_id: str, payload: Acknowledge,
    db: Session = Depends(get_session),
    principal: Principal = Depends(get_current_principal),
) -> dict:
    resolve_tenant_scope(principal, tenant_id)
    if not _may_file(tenant_id, principal):
        raise AppError("Your role may not record a submission", 403, "role_not_permitted")

    row = db.get(CtrFiling, filing_id)
    if row is None or row.tenant_id != tenant_id:
        raise AppError("Filing not found", 404, "not_found")
    if row.status == "superseded":
        raise AppError("This version was replaced by a later one; submit that instead",
                       409, "superseded")
    if row.reference_number:
        raise AppError(
            f"Already recorded as submitted under reference {row.reference_number}",
            409, "already_submitted")

    now = datetime.now(timezone.utc)
    row.status = "acknowledged"
    row.submitted_at = now
    row.submitted_by = principal.subject
    row.reference_number = payload.reference_number.strip()
    row.ack_note = payload.note
    db.commit()

    record_audit(
        service="analytics-service", action="ctr.acknowledged",
        actor=principal.subject, actor_role=principal.role, tenant_id=tenant_id,
        target_type="ctr_filing", target_id=filing_id, status="success",
        detail={"account": row.account, "period": row.period,
                "reference": row.reference_number})

    return {"id": row.id, "status": row.status,
            "reference_number": row.reference_number, "submitted_at": row.submitted_at}
