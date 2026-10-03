"""Detection control surface.

Detection normally runs as a worker (scripts/run_detection.py). This endpoint exists so an
operator - or a demo - can drain the queue on demand and see exactly what fired, which is
also the fastest way to prove that retuning a band in the console changed the outcome.
"""
from datetime import datetime

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from cp_common import (
    AppError, Principal, get_current_principal, get_session, record_audit,
    resolve_tenant_scope,
)
from cp_common.dynamic_roles import get_role, has_permission

from ..detection import run_until_empty
from ..detection import backfill as backfill_mod
from ..rules import active_rules, policy_values

router = APIRouter(prefix="/analytics", tags=["detection"])

# Re-scoring history is a control operation. Simulating is safe; replaying writes alerts.
# Each is its own grant on the tenant's role rows.
SIMULATE = "detection.simulate"
REPLAY = "detection.replay"


@router.post("/{tenant_id}/detection/run")
def run_detection(
    tenant_id: str,
    batch: int = Query(default=500, ge=1, le=5000),
    max_batches: int = Query(default=20, ge=1, le=200),
    db: Session = Depends(get_session),
    principal: Principal = Depends(get_current_principal),
) -> dict:
    resolve_tenant_scope(principal, tenant_id)
    # Running detection writes alerts and can open cases, so it is not a read.
    if not get_role(tenant_id, principal.role).can_admin_tenant:
        raise AppError("Running detection needs the administrator capability, which your role has not been given", 403,
                       "role_not_permitted")
    report = run_until_empty(db, tenant_id, batch=batch, max_batches=max_batches)
    record_audit(
        service="analytics-service", action="detection.run", actor=principal.subject,
        actor_role=principal.role, tenant_id=tenant_id, target_type="detection",
        target_id=tenant_id, status="success", detail=report.as_dict())
    return report.as_dict()


class BackfillIn(BaseModel):
    window_from: datetime
    window_to: datetime
    #: 'simulate' creates nothing; 'replay' writes the alerts that were missing.
    mode: str = Field(default="simulate", max_length=12)
    #: Try a different band without saving it, e.g. {"LAY-02": 25}. Applies to the
    #: simulation only - a replay must use the configuration that is actually in force,
    #: otherwise the alerts it writes could never be reproduced from the config history.
    thresholds: dict[str, float] = Field(default_factory=dict)


@router.post("/{tenant_id}/detection/backfill")
def backfill(
    tenant_id: str,
    body: BackfillIn,
    db: Session = Depends(get_session),
    principal: Principal = Depends(get_current_principal),
) -> dict:
    """Re-score a historical window.

    Simulating answers "what would this threshold have caught last quarter" and writes
    nothing. Replaying writes the alerts a fixed rule should have raised, and is
    restricted accordingly.
    """
    resolve_tenant_scope(principal, tenant_id)
    mode = (body.mode or "simulate").lower()
    needed = REPLAY if mode == "replay" else SIMULATE
    if not has_permission(tenant_id, principal.role, needed):
        raise AppError(
            "Replaying detection writes alerts and needs the replay permission, which "
            "your role has not been given."
            if mode == "replay" else "Your role may not re-score history.",
            403, "role_not_permitted")

    catalogue = dict(active_rules(tenant_id))
    if not catalogue:
        raise AppError(
            "The rule catalogue could not be read, so a backfill would score against "
            "nothing and report a clean window. Refused.", 503, "catalogue_unavailable")

    if body.thresholds:
        if mode == "replay":
            raise AppError(
                "Threshold overrides are for simulation only. A replay must use the "
                "configuration in force, or the alerts it writes could never be "
                "reproduced from the config history.", 400, "override_not_allowed")
        unknown = sorted(set(body.thresholds) - set(catalogue))
        if unknown:
            raise AppError(f"Unknown rule(s): {', '.join(unknown)}", 400, "unknown_rule")
        for rid, value in body.thresholds.items():
            catalogue[rid] = {**catalogue[rid], "threshold": float(value)}

    try:
        rep = backfill_mod.run(
            db, tenant_id=tenant_id, window_from=body.window_from,
            window_to=body.window_to, catalogue=catalogue,
            policy=policy_values(tenant_id), mode=mode, actor=principal.subject)
    except backfill_mod.BackfillRefused as exc:
        raise AppError(str(exc), 400, "backfill_refused")

    record_audit(
        service="analytics-service", action=f"detection.{mode}", actor=principal.subject,
        actor_role=principal.role, tenant_id=tenant_id, target_type="detection",
        target_id=tenant_id, status="success",
        detail={"mode": mode, "from": body.window_from.isoformat(),
                "to": body.window_to.isoformat(), "transactions": rep.transactions,
                "written": rep.written, "overrides": body.thresholds})
    return rep.as_dict()
