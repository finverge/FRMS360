"""LEA referral and recovery endpoints (BR-415)."""
from datetime import date, datetime, timezone

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from cp_common import (
    AppError, Principal, get_current_principal, get_session, record_audit,
    resolve_tenant_scope,
)
from cp_common.dynamic_roles import get_role

from ..lea_model import (
    AGENCIES, PROGRESSED, RECOVERY_MODES, REFERRAL_STATES, LeaReferral, RecoveryEntry,
)
from ..models import FRAUD_STATES, FactCase
from ..rules import policy_values

router = APIRouter(prefix="/analytics", tags=["recovery"])

RECORD_ROLES = ("investigator", "risk_manager", "principal_officer", "supervisor",
                "tenant_admin")


class ReferralIn(BaseModel):
    agency: str = Field(min_length=1, max_length=24)
    agency_office: str = Field(default="", max_length=200)
    complaint_ref: str = Field(default="", max_length=120)
    referred_on: date
    note: str = Field(default="", max_length=4000)


class ReferralUpdateIn(BaseModel):
    state: str = Field(min_length=1, max_length=24)
    fir_number: str = Field(default="", max_length=120)
    fir_date: date | None = None
    note: str = Field(default="", max_length=4000)


class RecoveryIn(BaseModel):
    #: Integer paise. Accepting rupees here would invite a float somewhere upstream.
    amount_paise: int = Field(gt=0)
    mode: str = Field(min_length=1, max_length=32)
    reference: str = Field(default="", max_length=200)
    recovered_on: date
    note: str = Field(default="", max_length=4000)


def _may(tenant_id: str, principal: Principal) -> bool:
    return principal.role in RECORD_ROLES or get_role(tenant_id, principal.role).can_admin_tenant


def _case(db: Session, tenant_id: str, case_id: str, *, lock: bool = False) -> FactCase:
    q = select(FactCase).where(FactCase.tenant_id == tenant_id,
                               FactCase.case_id == case_id)
    if lock:
        q = q.with_for_update()
    case = db.scalars(q).first()
    if case is None:
        raise AppError("Case not found", 404, "not_found")
    return case


def _entries(db: Session, tenant_id: str, case_id: str) -> list[RecoveryEntry]:
    return list(db.scalars(select(RecoveryEntry).where(
        RecoveryEntry.tenant_id == tenant_id,
        RecoveryEntry.case_id == case_id).order_by(
            RecoveryEntry.recovered_on)).all())


def _entry_total(db: Session, tenant_id: str, case_id: str) -> int:
    return int(db.scalar(select(func.coalesce(func.sum(RecoveryEntry.amount_paise), 0))
                         .where(RecoveryEntry.tenant_id == tenant_id,
                                RecoveryEntry.case_id == case_id)) or 0)


def _serialise(db: Session, tenant_id: str, case: FactCase, principal: Principal) -> dict:
    pol = policy_values(tenant_id)
    ref = db.scalars(select(LeaReferral).where(
        LeaReferral.tenant_id == tenant_id, LeaReferral.case_id == case.case_id)).first()
    entries = _entries(db, tenant_id, case.case_id)
    total = sum(e.amount_paise for e in entries)
    floor = int(pol.get("lea_referral_paise", 0) or 0)
    is_fraud = case.state in FRAUD_STATES

    return {
        "case_id": case.case_id,
        "case_state": case.state,
        "amount_paise": case.amount_paise,
        # Referral is expected once a declared fraud is at or above the tenant's own
        # board-approved floor. Below it, absence of a referral is not a gap.
        "referral_floor_paise": floor,
        "referral_expected": bool(is_fraud and floor and case.amount_paise >= floor),
        "referral": None if ref is None else {
            "agency": ref.agency, "agency_label": AGENCIES.get(ref.agency, ref.agency),
            "agency_office": ref.agency_office, "state": ref.state,
            "state_label": REFERRAL_STATES.get(ref.state, ref.state),
            "progressed": ref.state in PROGRESSED,
            "complaint_ref": ref.complaint_ref, "fir_number": ref.fir_number,
            "referred_on": ref.referred_on, "fir_date": ref.fir_date,
            "last_update": ref.last_update, "referred_by": ref.referred_by,
            "updated_at": ref.updated_at,
        },
        "recovered_paise": case.recovered_paise,
        "recovery_entries_paise": total,
        # The whole point of the feature: a recovered figure with nothing behind it.
        "recovery_evidenced": total == case.recovered_paise,
        "unevidenced_paise": max(0, (case.recovered_paise or 0) - total),
        "outstanding_paise": max(0, (case.amount_paise or 0) - (case.recovered_paise or 0)),
        "entries": [{
            "id": e.id, "amount_paise": e.amount_paise, "mode": e.mode,
            "mode_label": RECOVERY_MODES.get(e.mode, e.mode), "reference": e.reference,
            "recovered_on": e.recovered_on, "note": e.note,
            "recorded_by": e.recorded_by, "recorded_at": e.recorded_at,
        } for e in entries],
        "vocabulary": {"agencies": AGENCIES, "states": REFERRAL_STATES,
                       "modes": RECOVERY_MODES},
        "may_record": _may(tenant_id, principal),
    }


@router.get("/{tenant_id}/cases/{case_id}/recovery")
def get_recovery(
    tenant_id: str, case_id: str,
    db: Session = Depends(get_session),
    principal: Principal = Depends(get_current_principal),
) -> dict:
    resolve_tenant_scope(principal, tenant_id)
    return _serialise(db, tenant_id, _case(db, tenant_id, case_id), principal)


@router.post("/{tenant_id}/cases/{case_id}/recovery/referral")
def refer(
    tenant_id: str, case_id: str, payload: ReferralIn,
    db: Session = Depends(get_session),
    principal: Principal = Depends(get_current_principal),
) -> dict:
    resolve_tenant_scope(principal, tenant_id)
    if not _may(tenant_id, principal):
        raise AppError("Your role may not record a law-enforcement referral", 403,
                       "role_not_permitted")
    if payload.agency not in AGENCIES:
        raise AppError(f"Unknown agency '{payload.agency}'", 400, "unknown_agency")
    case = _case(db, tenant_id, case_id)
    if case.state not in FRAUD_STATES:
        raise AppError(
            "A case is referred to law enforcement once fraud has been declared. This "
            f"case is in '{case.state}'.", 409, "not_declared")
    if payload.referred_on > datetime.now(timezone.utc).date():
        raise AppError("A referral cannot be dated in the future", 400, "future_date")
    if db.scalars(select(LeaReferral).where(
            LeaReferral.tenant_id == tenant_id,
            LeaReferral.case_id == case_id)).first() is not None:
        raise AppError("This case already has a referral recorded", 409, "exists")

    row = LeaReferral(tenant_id=tenant_id, case_id=case_id, agency=payload.agency,
                      agency_office=payload.agency_office.strip(),
                      complaint_ref=payload.complaint_ref.strip(),
                      referred_on=payload.referred_on,
                      last_update=payload.note.strip(),
                      referred_by=principal.subject)
    db.add(row)
    db.commit()
    record_audit(
        service="analytics-service", action="lea.referred", actor=principal.subject,
        actor_role=principal.role, tenant_id=tenant_id, target_type="case",
        target_id=case_id, status="success",
        detail={"agency": payload.agency, "complaint_ref": row.complaint_ref})
    return _serialise(db, tenant_id, case, principal)


@router.post("/{tenant_id}/cases/{case_id}/recovery/referral/update")
def update_referral(
    tenant_id: str, case_id: str, payload: ReferralUpdateIn,
    db: Session = Depends(get_session),
    principal: Principal = Depends(get_current_principal),
) -> dict:
    resolve_tenant_scope(principal, tenant_id)
    if not _may(tenant_id, principal):
        raise AppError("Your role may not update a referral", 403, "role_not_permitted")
    if payload.state not in REFERRAL_STATES:
        raise AppError(f"Unknown referral state '{payload.state}'", 400, "unknown_state")
    case = _case(db, tenant_id, case_id)
    ref = db.scalars(select(LeaReferral).where(
        LeaReferral.tenant_id == tenant_id, LeaReferral.case_id == case_id)).first()
    if ref is None:
        raise AppError("No referral has been recorded for this case", 409, "not_referred")
    if payload.state == "fir_registered" and not (payload.fir_number.strip()
                                                  or ref.fir_number):
        # An FIR without its number cannot be followed up, and is the single fact the
        # agency will ask for.
        raise AppError("An FIR number is required to record an FIR as registered", 400,
                       "fir_number_required")

    ref.state = payload.state
    if payload.fir_number.strip():
        ref.fir_number = payload.fir_number.strip()
    if payload.fir_date:
        ref.fir_date = payload.fir_date
    if payload.note.strip():
        ref.last_update = payload.note.strip()
    db.commit()
    record_audit(
        service="analytics-service", action="lea.updated", actor=principal.subject,
        actor_role=principal.role, tenant_id=tenant_id, target_type="case",
        target_id=case_id, status="success",
        detail={"state": ref.state, "fir_number": ref.fir_number})
    return _serialise(db, tenant_id, case, principal)


@router.post("/{tenant_id}/cases/{case_id}/recovery/entries")
def add_recovery(
    tenant_id: str, case_id: str, payload: RecoveryIn,
    db: Session = Depends(get_session),
    principal: Principal = Depends(get_current_principal),
) -> dict:
    """Record a substantiated recovery and re-derive the case total from its entries."""
    resolve_tenant_scope(principal, tenant_id)
    if not _may(tenant_id, principal):
        raise AppError("Your role may not record a recovery", 403, "role_not_permitted")
    if payload.mode not in RECOVERY_MODES:
        raise AppError(f"Unknown recovery mode '{payload.mode}'", 400, "unknown_mode")
    # Locked: the case total is re-derived below, and two concurrent entries must not
    # both read the same pre-image and write the same sum.
    case = _case(db, tenant_id, case_id, lock=True)
    if payload.recovered_on > datetime.now(timezone.utc).date():
        raise AppError("A recovery cannot be dated in the future", 400, "future_date")

    total = _entry_total(db, tenant_id, case_id) + payload.amount_paise
    if total > (case.amount_paise or 0):
        # Recovering more than was lost is a data error every time, and it would make
        # net_fraud_value negative on the board pack.
        raise AppError(
            f"Recoveries would total {total} paise against a fraud of "
            f"{case.amount_paise} paise. A case cannot recover more than it lost.",
            409, "exceeds_loss")

    db.add(RecoveryEntry(
        tenant_id=tenant_id, case_id=case_id, amount_paise=payload.amount_paise,
        mode=payload.mode, reference=payload.reference.strip(),
        recovered_on=payload.recovered_on, note=payload.note.strip(),
        recorded_by=principal.subject))
    # The column becomes the sum of its entries, so every dashboard reading
    # recovered_paise is reading something substantiated.
    case.recovered_paise = total
    db.commit()

    record_audit(
        service="analytics-service", action="recovery.recorded", actor=principal.subject,
        actor_role=principal.role, tenant_id=tenant_id, target_type="case",
        target_id=case_id, status="success",
        detail={"amount_paise": payload.amount_paise, "mode": payload.mode,
                "reference": payload.reference.strip(), "case_total_paise": total})
    return _serialise(db, tenant_id, case, principal)


@router.get("/{tenant_id}/recovery/register")
def register(
    tenant_id: str,
    db: Session = Depends(get_session),
    principal: Principal = Depends(get_current_principal),
) -> dict:
    """Referral and recovery position across every declared fraud.

    Two gaps are surfaced deliberately: frauds above the referral floor with no complaint
    lodged, and recovered amounts with no entry behind them.
    """
    resolve_tenant_scope(principal, tenant_id)
    floor = int(policy_values(tenant_id).get("lea_referral_paise", 0) or 0)
    rows = db.execute(text("""
        SELECT c.case_id, c.state, c.amount_paise, c.recovered_paise, c.decision_ts,
               r.agency, r.state AS referral_state, r.fir_number, r.referred_on,
               COALESCE(e.total, 0) AS entries_paise, COALESCE(e.n, 0) AS entry_count
          FROM analytics.fact_case c
          LEFT JOIN cases.lea_referrals r
                 ON r.tenant_id = c.tenant_id AND r.case_id = c.case_id
          LEFT JOIN (SELECT case_id, SUM(amount_paise) AS total, COUNT(*) AS n
                       FROM cases.recovery_entries WHERE tenant_id = :t
                      GROUP BY case_id) e ON e.case_id = c.case_id
         WHERE c.tenant_id = :t AND c.state = ANY(:states)
         ORDER BY c.amount_paise DESC
    """), {"t": tenant_id, "states": list(FRAUD_STATES)}).mappings().all()

    items = []
    for r in rows:
        expected = bool(floor and r["amount_paise"] >= floor)
        items.append({
            "case_id": r["case_id"], "state": r["state"],
            "amount_paise": r["amount_paise"],
            "recovered_paise": r["recovered_paise"],
            "entries_paise": int(r["entries_paise"]),
            "entry_count": int(r["entry_count"]),
            "recovery_evidenced": int(r["entries_paise"]) == (r["recovered_paise"] or 0),
            "unevidenced_paise": max(0, (r["recovered_paise"] or 0)
                                     - int(r["entries_paise"])),
            "referral_expected": expected,
            "referral_missing": expected and r["agency"] is None,
            "agency": r["agency"], "referral_state": r["referral_state"],
            "fir_number": r["fir_number"], "referred_on": r["referred_on"],
            "referral_stalled": bool(r["agency"] and r["referral_state"] == "referred"),
        })
    # Worst first: a fraud over the floor with no complaint lodged at all.
    items.sort(key=lambda i: (not i["referral_missing"], i["recovery_evidenced"],
                              -(i["amount_paise"] or 0)))

    return {
        "referral_floor_paise": floor,
        "count": len(items),
        "referral_expected": sum(1 for i in items if i["referral_expected"]),
        "referral_missing": sum(1 for i in items if i["referral_missing"]),
        "referral_stalled": sum(1 for i in items if i["referral_stalled"]),
        "unevidenced_cases": sum(1 for i in items if not i["recovery_evidenced"]),
        "unevidenced_paise": sum(i["unevidenced_paise"] for i in items),
        "recovered_paise": sum(i["recovered_paise"] or 0 for i in items),
        "items": items,
    }
