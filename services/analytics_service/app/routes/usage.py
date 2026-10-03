"""Usage reporting (BR-110)."""
from datetime import date, datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from cp_common import (
    AppError, Principal, get_current_principal, get_session, resolve_tenant_scope,
)
from cp_common.dynamic_roles import has_permission

from .. import usage_service
from ..usage_model import METERS

router = APIRouter(prefix="/analytics", tags=["usage"])

# Usage is commercial data: a tenant sees its own when a role of its holds "usage.view";
# platform staff see any.
PERMISSION = "usage.view"


@router.get("/{tenant_id}/usage")
def usage(
    tenant_id: str,
    date_from: date | None = Query(default=None),
    date_to: date | None = Query(default=None),
    db: Session = Depends(get_session),
    principal: Principal = Depends(get_current_principal),
) -> dict:
    """Metered usage for a period, in the shape an invoice is built from.

    Each meter carries the basis it was counted on, because that is the first thing a
    customer asks when they query a line.
    """
    resolve_tenant_scope(principal, tenant_id)
    if not has_permission(tenant_id, principal.role, PERMISSION):
        raise AppError("Your role may not view billing usage", 403,
                       "role_not_permitted")
    today = datetime.now(timezone.utc).date()
    start = date_from or today.replace(day=1)
    end = date_to or today
    if end < start:
        raise AppError("The period ends before it starts", 400, "bad_period")
    if (end - start).days > 400:
        raise AppError("Report at most 400 days at a time", 400, "period_too_long")
    return usage_service.report(db, tenant_id, start, end)


@router.get("/{tenant_id}/usage/meters")
def meters(
    tenant_id: str,
    principal: Principal = Depends(get_current_principal),
) -> list[dict]:
    """What is metered and how, independent of any period."""
    resolve_tenant_scope(principal, tenant_id)
    return [{"meter": m.key, "label": m.label, "unit": m.unit, "basis": m.basis}
            for m in METERS]
