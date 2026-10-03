"""Case workflow endpoints - the only way a case's state may change."""
from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from cp_common import (
    AppError, Principal, get_current_principal, get_session, record_audit,
    resolve_tenant_scope,
)
from cp_common.dynamic_roles import get_role, has_permission

from .. import workflow as wf
from ..accountability_model import StaffAccountability
from ..documents_model import CaseDocument
from ..models import FactCase
from ..rules import policy_values
from ..workflow_model import CaseTransition

router = APIRouter(prefix="/analytics", tags=["cases"])


class TransitionIn(BaseModel):
    action: str = Field(min_length=1, max_length=32)
    reason: str = Field(default="", max_length=4000)


class AssignIn(BaseModel):
    assignee: str = Field(default="", max_length=64)


def _is_tenant_user(db: Session, tenant_id: str, email: str) -> bool:
    """Is this someone who can actually sign in to this tenant?

    Read through the internal API rather than the table: analytics has no grant on the
    tenant schema, and should not - it works from tenant_id alone.
    """
    import httpx

    from cp_common import settings

    try:
        r = httpx.get(f"{settings.tenant_service_url}/tenants/{tenant_id}/users",
                      headers={"x-internal-key": settings.internal_api_key},
                      timeout=5.0)
        r.raise_for_status()
        return any((u.get("email") or "").lower() == email.lower() for u in r.json())
    except Exception:  # noqa: BLE001
        # If the register cannot be reached, do not block an investigator from taking
        # ownership of a case. Refusing here would make an outage look like a
        # permissions problem.
        return True


def _case(db: Session, tenant_id: str, case_id: str, *, lock: bool = False) -> FactCase:
    """Load a case, optionally taking a row lock for the duration of the transaction.

    The lock is not optional for state changes. This service runs as several replicas
    behind a load balancer, so two requests for the same case land in different processes
    with no shared memory between them. Without SELECT ... FOR UPDATE, a maker-checker
    approval arriving twice, or two investigators clicking at once, would both read the
    same state and both apply - defeating the guard entirely. The lock serialises them so
    the second attempt re-reads the state the first one left behind and is refused.
    """
    if lock:
        case = db.scalar(
            select(FactCase)
            .where(FactCase.case_id == case_id, FactCase.tenant_id == tenant_id)
            .with_for_update())
    else:
        case = db.get(FactCase, case_id)
    if case is None or case.tenant_id != tenant_id:
        raise AppError("Case not found", 404, "not_found")
    return case


def _doc_types(db: Session, tenant_id: str, case_id: str) -> set[str]:
    return set(db.scalars(
        select(CaseDocument.doc_type).where(CaseDocument.tenant_id == tenant_id,
                                            CaseDocument.case_id == case_id)))


def _state_since(db: Session, tenant_id: str, case_id: str, case: FactCase) -> datetime:
    """When the case entered its current state - the clock lateness is measured from."""
    last = db.scalar(
        select(CaseTransition)
        .where(CaseTransition.tenant_id == tenant_id,
               CaseTransition.case_id == case_id,
               CaseTransition.status == "applied")
        .order_by(CaseTransition.created_at.desc()).limit(1))
    return last.created_at if last else case.opened_ts


def _pending(db: Session, tenant_id: str, case_id: str, action: str) -> CaseTransition | None:
    """An open proposal awaiting a second approver."""
    return db.scalar(
        select(CaseTransition)
        .where(CaseTransition.tenant_id == tenant_id,
               CaseTransition.case_id == case_id,
               CaseTransition.action == action,
               CaseTransition.status == "proposed")
        .order_by(CaseTransition.created_at.desc()).limit(1))


def _accountability_status(db: Session, tenant_id: str, case_id: str) -> str:
    """The examination's status, or "" if nobody has started one."""
    return db.scalar(select(StaffAccountability.status).where(
        StaffAccountability.tenant_id == tenant_id,
        StaffAccountability.case_id == case_id)) or ""


def _context(db: Session, tenant_id: str, case: FactCase, principal: Principal,
             action: str = "") -> wf.Context:
    pend = _pending(db, tenant_id, case.case_id, action) if action else None
    return wf.Context(
        state=case.state, role=principal.role, actor=principal.subject,
        now=datetime.now(timezone.utc), policy=policy_values(tenant_id),
        doc_types=_doc_types(db, tenant_id, case.case_id),
        response_due_ts=case.response_due_ts,
        accountability_status=_accountability_status(db, tenant_id, case.case_id),
        pending_actor=pend.actor if pend else None,
        pending_role=pend.actor_role if pend else None,
        state_since=_state_since(db, tenant_id, case.case_id, case),
        permissions=frozenset(get_role(tenant_id, principal.role).permissions),
    )


def _log(db: Session, tenant_id: str, case: FactCase, principal: Principal, *,
         action: str, status: str, to_state: str = "", reason: str = "",
         refusal_code: str = "", breached: bool = False, allowed: int = 0,
         approves_id: str | None = None) -> CaseTransition:
    row = CaseTransition(
        tenant_id=tenant_id, case_id=case.case_id, action=action,
        from_state=case.state, to_state=to_state, actor=principal.subject,
        actor_role=principal.role, status=status, refusal_code=refusal_code,
        reason=reason, breached_policy=breached, breach_days_allowed=allowed,
        approves_id=approves_id)
    db.add(row)
    return row


@router.get("/{tenant_id}/cases/{case_id}/workflow")
def workflow_state(
    tenant_id: str,
    case_id: str,
    db: Session = Depends(get_session),
    principal: Principal = Depends(get_current_principal),
) -> dict:
    """The case's position in the lifecycle and what this user may do next.

    Blocked actions are returned *with the reason they are blocked* rather than hidden.
    A hidden control looks like a missing feature; a disabled one with "the response
    window is open until 14 Aug" teaches the process.
    """
    resolve_tenant_scope(principal, tenant_id)
    case = _case(db, tenant_id, case_id)
    pol = policy_values(tenant_id)

    actions = []
    for tr in wf.available(case.state):
        ctx = _context(db, tenant_id, case, principal, tr.action)
        try:
            wf.check(tr.action, ctx, reason="placeholder" if tr.requires_reason else "")
            allowed, why, code = True, "", ""
        except wf.TransitionRefused as exc:
            # approval_pending is not a blocker - it is what pressing the button does.
            allowed = exc.code == "approval_pending"
            why, code = ("", "") if allowed else (exc.message, exc.code)
        pend = _pending(db, tenant_id, case_id, tr.action)
        actions.append({
            "action": tr.action, "label": tr.label, "to_state": tr.dst,
            "allowed": allowed, "blocked_reason": why, "blocked_code": code,
            "requires_reason": tr.requires_reason,
            "requires_document": tr.requires_document,
            "requires_second_approval": tr.requires_second_approval,
            "proposed_by": pend.actor if pend else None,
            "you_proposed_it": bool(pend and pend.actor == principal.subject),
        })

    return {
        "case_id": case.case_id,
        "state": case.state,
        "terminal": case.state in wf.TERMINAL,
        "assignee": case.assignee,
        "rfa_flag": case.rfa_flag,
        "documents": sorted(_doc_types(db, tenant_id, case_id)),
        "clocks": {
            "show_cause_ts": case.show_cause_ts,
            "response_due_ts": case.response_due_ts,
            "decision_ts": case.decision_ts,
            "fmr_due_ts": case.fmr_due_ts,
            "fmr_filed_ts": case.fmr_filed_ts,
            "str_due_ts": case.str_due_ts,
            "str_filed_ts": case.str_filed_ts,
            "accountability_due_ts": case.accountability_due_ts,
        },
        "accountability_status": _accountability_status(db, tenant_id, case_id),
        # Shown so a user can see the window is the bank's own board-approved figure.
        "policy": {k: pol.get(k) for k in (
            "natural_justice_days", "show_cause_within_days",
            "fmr_filing_days", "str_filing_days", "staff_accountability_days")},
        "actions": actions,
    }


@router.post("/{tenant_id}/cases/{case_id}/transition")
def transition(
    tenant_id: str,
    case_id: str,
    payload: TransitionIn,
    db: Session = Depends(get_session),
    principal: Principal = Depends(get_current_principal),
) -> dict:
    resolve_tenant_scope(principal, tenant_id)
    # Locked for the whole transaction: the guards below are only meaningful if no other
    # replica can change the case between the check and the write.
    case = _case(db, tenant_id, case_id, lock=True)
    ctx = _context(db, tenant_id, case, principal, payload.action)

    try:
        tr = wf.check(payload.action, ctx, reason=payload.reason)
    except wf.TransitionRefused as exc:
        if exc.code == "approval_pending":
            # Not a failure: this is the maker half of maker-checker.
            row = _log(db, tenant_id, case, principal, action=payload.action,
                       status="proposed", reason=payload.reason)
            db.commit()
            record_audit(
                service="analytics-service", action="case.propose", actor=principal.subject,
                actor_role=principal.role, tenant_id=tenant_id, target_type="case",
                target_id=case_id, status="success",
                detail={"action": payload.action, "state": case.state})
            return {"outcome": "proposed", "state": case.state,
                    "message": exc.message, "transition_id": row.id}
        # A refusal is itself a record - especially repeated attempts to skip a window.
        _log(db, tenant_id, case, principal, action=payload.action, status="refused",
             reason=payload.reason, refusal_code=exc.code)
        db.commit()
        record_audit(
            service="analytics-service", action="case.transition", actor=principal.subject,
            actor_role=principal.role, tenant_id=tenant_id, target_type="case",
            target_id=case_id, status="refused",
            detail={"action": payload.action, "code": exc.code})
        raise AppError(exc.message, 409, exc.code)

    breached, allowed_days = wf.is_late(tr, ctx)
    pend = _pending(db, tenant_id, case_id, payload.action)

    case.state = tr.dst
    if tr.action == "flag_rfa":
        case.rfa_flag = True
    elif tr.action == "revoke_rfa":
        case.rfa_flag = False
    elif tr.action == "issue_show_cause":
        case.show_cause_ts = ctx.now
    elif tr.action == "declare_fraud":
        case.decision_ts = ctx.now
    elif tr.action == "file_fmr":
        case.fmr_filed_ts = ctx.now
    elif tr.action == "close_case":
        case.closed_ts = ctx.now
    elif tr.action in ("exonerate", "close_no_fraud"):
        case.closed_ts = ctx.now
    elif tr.action == "reopen":
        case.closed_ts = None

    for field_name, when in wf.clocks_for(tr, ctx).items():
        setattr(case, field_name, when)

    if pend is not None:
        pend.status = "approved"

    row = _log(db, tenant_id, case, principal, action=payload.action, status="applied",
               to_state=tr.dst, reason=payload.reason, breached=breached,
               allowed=allowed_days, approves_id=pend.id if pend else None)
    # from_state was captured before the mutation above only if we log first; keep the
    # record truthful about where the case came from.
    row.from_state = ctx.state
    db.commit()

    record_audit(
        service="analytics-service", action="case.transition", actor=principal.subject,
        actor_role=principal.role, tenant_id=tenant_id, target_type="case",
        target_id=case_id, status="success",
        detail={"action": payload.action, "from": ctx.state, "to": tr.dst,
                "breached_policy": breached,
                "approved_proposal_by": pend.actor if pend else None})

    return {
        "outcome": "applied", "state": case.state, "from_state": ctx.state,
        "breached_policy": breached, "days_allowed": allowed_days,
        "transition_id": row.id,
        "message": tr.label + " recorded." + (
            " Note: this was outside the " + str(allowed_days) +
            "-day window your board approved, and has been recorded as a breach."
            if breached else ""),
    }


@router.post("/{tenant_id}/cases/{case_id}/assign")
def assign(
    tenant_id: str,
    case_id: str,
    payload: AssignIn,
    db: Session = Depends(get_session),
    principal: Principal = Depends(get_current_principal),
) -> dict:
    """Assignment is not a state change, but it is still accountability."""
    resolve_tenant_scope(principal, tenant_id)
    if not has_permission(tenant_id, principal.role, "case.assign"):
        raise AppError("Your role may not assign cases", 403, "role_not_permitted")
    # A case assigned to somebody who does not exist looks owned and is not: it drops
    # out of every "unassigned" view while no one is actually working it, and any
    # notification aimed at the owner goes nowhere.
    assignee = payload.assignee.strip()
    if assignee and not _is_tenant_user(db, tenant_id, assignee):
        raise AppError(
            f"'{assignee}' is not a user of this tenant. Assign the case to someone "
            "who can sign in and act on it.", 400, "unknown_assignee")

    case = _case(db, tenant_id, case_id, lock=True)
    before, case.assignee = case.assignee, assignee
    db.commit()
    record_audit(
        service="analytics-service", action="case.assign", actor=principal.subject,
        actor_role=principal.role, tenant_id=tenant_id, target_type="case",
        target_id=case_id, status="success",
        detail={"from": before, "to": case.assignee})
    return {"case_id": case_id, "assignee": case.assignee}


@router.get("/{tenant_id}/cases/{case_id}/history")
def history(
    tenant_id: str,
    case_id: str,
    db: Session = Depends(get_session),
    principal: Principal = Depends(get_current_principal),
) -> dict:
    """How this case reached its current state, refusals included."""
    resolve_tenant_scope(principal, tenant_id)
    _case(db, tenant_id, case_id)
    rows = db.scalars(
        select(CaseTransition)
        .where(CaseTransition.tenant_id == tenant_id, CaseTransition.case_id == case_id)
        .order_by(CaseTransition.created_at)).all()
    return {"case_id": case_id, "rows": [
        {"id": r.id, "action": r.action,
         "label": wf.BY_ACTION[r.action].label if r.action in wf.BY_ACTION else r.action,
         "from_state": r.from_state, "to_state": r.to_state, "actor": r.actor,
         "actor_role": r.actor_role, "status": r.status, "reason": r.reason,
         "refusal_code": r.refusal_code, "breached_policy": r.breached_policy,
         "breach_days_allowed": r.breach_days_allowed, "approves_id": r.approves_id,
         "created_at": r.created_at}
        for r in rows]}
