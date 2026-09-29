"""Staff accountability examination endpoints (BR-414).

See ``accountability_model`` for why this gates ``close_case`` and never ``file_fmr``.
"""
from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from cp_common import (
    AppError, Principal, get_current_principal, get_session, record_audit,
    resolve_tenant_scope,
)
from cp_common.dynamic_roles import get_role

from ..accountability_model import (
    ACTIONS, ADVERSE, FINDINGS, AccountabilityFinding, StaffAccountability,
)
from ..models import FactCase
from ..rules import policy_values

router = APIRouter(prefix="/analytics", tags=["accountability"])

#: Who may run the exercise. Broader than who may conclude it: gathering findings is
#: casework, signing off on them is an act of authority.
EXAMINER_ROLES = ("investigator", "risk_manager", "principal_officer", "supervisor",
                  "tenant_admin")
CONCLUDER_ROLES = ("risk_manager", "principal_officer", "tenant_admin")


class OpenIn(BaseModel):
    examiner: str = Field(default="", max_length=255)


class FindingIn(BaseModel):
    staff_ref: str = Field(min_length=1, max_length=64)
    staff_name: str = Field(min_length=1, max_length=255)
    role_at_time: str = Field(default="", max_length=120)
    branch: str = Field(default="", max_length=120)
    finding: str = Field(min_length=1, max_length=24)
    action_taken: str = Field(default="pending", max_length=24)
    note: str = Field(default="", max_length=4000)


class ConcludeIn(BaseModel):
    conclusion: str = Field(min_length=1, max_length=8000)
    systemic_lapse: bool = False
    systemic_note: str = Field(default="", max_length=4000)


def _may(tenant_id: str, principal: Principal, roles: tuple[str, ...]) -> bool:
    return principal.role in roles or get_role(tenant_id, principal.role).can_admin_tenant


def _case(db: Session, tenant_id: str, case_id: str) -> FactCase:
    case = db.scalars(select(FactCase).where(
        FactCase.tenant_id == tenant_id, FactCase.case_id == case_id)).first()
    if case is None:
        raise AppError("Case not found", 404, "not_found")
    return case


def _exam(db: Session, tenant_id: str, case_id: str) -> StaffAccountability | None:
    return db.scalars(select(StaffAccountability).where(
        StaffAccountability.tenant_id == tenant_id,
        StaffAccountability.case_id == case_id)).first()


def _findings(db: Session, tenant_id: str, exam_id: str) -> list[AccountabilityFinding]:
    return list(db.scalars(select(AccountabilityFinding).where(
        AccountabilityFinding.tenant_id == tenant_id,
        AccountabilityFinding.examination_id == exam_id).order_by(
            AccountabilityFinding.recorded_at)).all())


def _aware(ts):
    if ts is None:
        return None
    return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)


def _serialise(exam: StaffAccountability | None, findings, case: FactCase,
               pol: dict, principal: Principal | None = None) -> dict:
    due = _aware(case.accountability_due_ts)
    now = datetime.now(timezone.utc)
    concluded = _aware(exam.concluded_at) if exam else None
    # Overdue means "not concluded, and past due" - a concluded-late examination is a
    # breach on the record, not a live overdue item.
    overdue = bool(due and not concluded and now > due)
    return {
        "case_id": case.case_id,
        "case_state": case.state,
        # "" rather than a status, so "nobody has started" is distinguishable from
        # "started and going nowhere".
        "status": exam.status if exam else "",
        "required": case.state in ("fraud_declared", "fmr_reported", "closed_fraud"),
        "due_ts": due,
        "overdue": overdue,
        "days_remaining": (int((due - now).total_seconds() // 86400)
                           if due and not concluded else None),
        "window_days": pol.get("staff_accountability_days"),
        "examiner": exam.examiner if exam else "",
        "opened_by": exam.opened_by if exam else "",
        "opened_at": exam.opened_at if exam else None,
        "conclusion": exam.conclusion if exam else "",
        "systemic_lapse": bool(exam.systemic_lapse) if exam else False,
        "systemic_note": exam.systemic_note if exam else "",
        "concluded_by": exam.concluded_by if exam else "",
        "concluded_at": concluded,
        "breached_policy": bool(exam.breached_policy) if exam else False,
        "breach_days_allowed": exam.breach_days_allowed if exam else 0,
        "staff_implicated": sum(1 for f in findings if f.finding in ADVERSE),
        "findings": [{
            "id": f.id, "staff_ref": f.staff_ref, "staff_name": f.staff_name,
            "role_at_time": f.role_at_time, "branch": f.branch,
            "finding": f.finding, "finding_label": FINDINGS.get(f.finding, f.finding),
            "adverse": f.finding in ADVERSE,
            "action_taken": f.action_taken,
            "action_label": ACTIONS.get(f.action_taken, f.action_taken),
            "note": f.note, "recorded_by": f.recorded_by, "recorded_at": f.recorded_at,
        } for f in findings],
        "vocabulary": {"findings": FINDINGS, "actions": ACTIONS},
        # So the console can disable an action *with its reason* rather than offer a
        # button that fails on save. Same rule the lifecycle follows (BR-410).
        "may_examine": bool(principal and _may(case.tenant_id, principal, EXAMINER_ROLES)),
        "may_conclude": bool(principal and _may(case.tenant_id, principal, CONCLUDER_ROLES)),
        "conclude_requires": ("Concluding requires a Fraud Risk Manager, Principal "
                              "Officer or Tenant Administrator."),
    }


@router.get("/{tenant_id}/cases/{case_id}/accountability")
def get_accountability(
    tenant_id: str, case_id: str,
    db: Session = Depends(get_session),
    principal: Principal = Depends(get_current_principal),
) -> dict:
    resolve_tenant_scope(principal, tenant_id)
    case = _case(db, tenant_id, case_id)
    exam = _exam(db, tenant_id, case_id)
    findings = _findings(db, tenant_id, exam.id) if exam else []
    return _serialise(exam, findings, case, policy_values(tenant_id), principal)


@router.post("/{tenant_id}/cases/{case_id}/accountability/open")
def open_examination(
    tenant_id: str, case_id: str, payload: OpenIn,
    db: Session = Depends(get_session),
    principal: Principal = Depends(get_current_principal),
) -> dict:
    resolve_tenant_scope(principal, tenant_id)
    if not _may(tenant_id, principal, EXAMINER_ROLES):
        raise AppError("Your role may not run a staff accountability examination", 403,
                       "role_not_permitted")
    case = _case(db, tenant_id, case_id)
    if case.state not in ("fraud_declared", "fmr_reported", "closed_fraud"):
        # Examining staff over an allegation that has not been sustained would be
        # prejudging it - and the record would outlive the exoneration.
        raise AppError(
            "Staff accountability is examined once fraud has been declared. This case is "
            f"in '{case.state}'.", 409, "not_declared")
    if _exam(db, tenant_id, case_id) is not None:
        raise AppError("An examination is already open for this case", 409, "exists")

    row = StaffAccountability(tenant_id=tenant_id, case_id=case_id,
                              opened_by=principal.subject,
                              examiner=payload.examiner.strip() or principal.subject)
    db.add(row)
    db.commit()
    record_audit(
        service="analytics-service", action="accountability.opened",
        actor=principal.subject, actor_role=principal.role, tenant_id=tenant_id,
        target_type="case", target_id=case_id, status="success",
        detail={"examiner": row.examiner})
    return _serialise(row, [], case, policy_values(tenant_id), principal)


@router.post("/{tenant_id}/cases/{case_id}/accountability/findings")
def add_finding(
    tenant_id: str, case_id: str, payload: FindingIn,
    db: Session = Depends(get_session),
    principal: Principal = Depends(get_current_principal),
) -> dict:
    resolve_tenant_scope(principal, tenant_id)
    if not _may(tenant_id, principal, EXAMINER_ROLES):
        raise AppError("Your role may not record accountability findings", 403,
                       "role_not_permitted")
    if payload.finding not in FINDINGS:
        raise AppError(f"Unknown finding '{payload.finding}'", 400, "unknown_finding")
    if payload.action_taken not in ACTIONS:
        raise AppError(f"Unknown action '{payload.action_taken}'", 400, "unknown_action")

    case = _case(db, tenant_id, case_id)
    exam = _exam(db, tenant_id, case_id)
    if exam is None:
        raise AppError("No examination has been opened for this case", 409, "not_open")
    if exam.status == "concluded":
        raise AppError(
            "This examination has been concluded and its findings are final.", 409,
            "concluded")

    row = AccountabilityFinding(
        tenant_id=tenant_id, case_id=case_id, examination_id=exam.id,
        staff_ref=payload.staff_ref.strip(), staff_name=payload.staff_name.strip(),
        role_at_time=payload.role_at_time.strip(), branch=payload.branch.strip(),
        finding=payload.finding, action_taken=payload.action_taken,
        note=payload.note.strip(), recorded_by=principal.subject)
    db.add(row)
    db.commit()
    record_audit(
        service="analytics-service", action="accountability.finding_recorded",
        actor=principal.subject, actor_role=principal.role, tenant_id=tenant_id,
        target_type="case", target_id=case_id, status="success",
        # The staff reference is the point of the record; the note may hold detail that
        # does not belong in an audit line.
        detail={"staff_ref": row.staff_ref, "finding": row.finding,
                "action_taken": row.action_taken})
    return _serialise(exam, _findings(db, tenant_id, exam.id), case,
                      policy_values(tenant_id), principal)


@router.delete("/{tenant_id}/cases/{case_id}/accountability/findings/{finding_id}")
def remove_finding(
    tenant_id: str, case_id: str, finding_id: str,
    db: Session = Depends(get_session),
    principal: Principal = Depends(get_current_principal),
) -> dict:
    """Remove a finding entered in error. Only while the examination is open, and audited.

    There is no equivalent once concluded: at that point the findings are the bank's
    answer to a supervisor.
    """
    resolve_tenant_scope(principal, tenant_id)
    if not _may(tenant_id, principal, EXAMINER_ROLES):
        raise AppError("Your role may not amend accountability findings", 403,
                       "role_not_permitted")
    case = _case(db, tenant_id, case_id)
    exam = _exam(db, tenant_id, case_id)
    if exam is None:
        raise AppError("No examination has been opened for this case", 409, "not_open")
    if exam.status == "concluded":
        raise AppError(
            "This examination has been concluded; its findings cannot be withdrawn.",
            409, "concluded")
    row = db.scalars(select(AccountabilityFinding).where(
        AccountabilityFinding.tenant_id == tenant_id,
        AccountabilityFinding.examination_id == exam.id,
        AccountabilityFinding.id == finding_id)).first()
    if row is None:
        raise AppError("Finding not found", 404, "not_found")

    detail = {"staff_ref": row.staff_ref, "finding": row.finding,
              "recorded_by": row.recorded_by}
    db.delete(row)
    db.commit()
    record_audit(
        service="analytics-service", action="accountability.finding_withdrawn",
        actor=principal.subject, actor_role=principal.role, tenant_id=tenant_id,
        target_type="case", target_id=case_id, status="success", detail=detail)
    return _serialise(exam, _findings(db, tenant_id, exam.id), case,
                      policy_values(tenant_id), principal)


@router.post("/{tenant_id}/cases/{case_id}/accountability/conclude")
def conclude(
    tenant_id: str, case_id: str, payload: ConcludeIn,
    db: Session = Depends(get_session),
    principal: Principal = Depends(get_current_principal),
) -> dict:
    resolve_tenant_scope(principal, tenant_id)
    if not _may(tenant_id, principal, CONCLUDER_ROLES):
        raise AppError(
            "Concluding a staff accountability examination requires a Fraud Risk "
            "Manager, Principal Officer or Tenant Administrator.", 403,
            "role_not_permitted")
    case = _case(db, tenant_id, case_id)
    exam = _exam(db, tenant_id, case_id)
    if exam is None:
        raise AppError("No examination has been opened for this case", 409, "not_open")
    if exam.status == "concluded":
        raise AppError("This examination is already concluded", 409, "concluded")

    findings = _findings(db, tenant_id, exam.id)
    if not findings:
        # An examination with no findings is indistinguishable from one nobody ran.
        # "No lapse established" against a named person is an available finding, and
        # recording it is the point.
        raise AppError(
            "Record at least one finding before concluding. If no member of staff was at "
            "fault, record that against the person examined - an examination with no "
            "findings cannot be told apart from one that never happened.",
            409, "no_findings")

    now = datetime.now(timezone.utc)
    allowed = int(policy_values(tenant_id).get("staff_accountability_days", 0) or 0)
    due = _aware(case.accountability_due_ts)
    exam.status = "concluded"
    exam.conclusion = payload.conclusion.strip()
    exam.systemic_lapse = payload.systemic_lapse
    exam.systemic_note = payload.systemic_note.strip()
    exam.concluded_by = principal.subject
    exam.concluded_at = now
    # Late is recorded, never blocked - the same treatment the show-cause notice gets.
    exam.breached_policy = bool(due and now > due)
    exam.breach_days_allowed = allowed
    db.commit()

    record_audit(
        service="analytics-service", action="accountability.concluded",
        actor=principal.subject, actor_role=principal.role, tenant_id=tenant_id,
        target_type="case", target_id=case_id, status="success",
        detail={"staff_examined": len(findings),
                "staff_implicated": sum(1 for f in findings if f.finding in ADVERSE),
                "systemic_lapse": exam.systemic_lapse,
                "breached_policy": exam.breached_policy})
    return _serialise(exam, findings, case, policy_values(tenant_id), principal)


@router.get("/{tenant_id}/accountability/register")
def register(
    tenant_id: str,
    db: Session = Depends(get_session),
    principal: Principal = Depends(get_current_principal),
) -> dict:
    """Every declared fraud and where its accountability examination stands.

    Ordered worst-first: overdue and unexamined at the top. This is the view a Special
    Committee or an inspection asks for, and the one that makes an examination nobody
    started visible rather than merely absent.
    """
    resolve_tenant_scope(principal, tenant_id)
    pol = policy_values(tenant_id)
    rows = db.execute(text("""
        SELECT c.case_id, c.state, c.amount_paise, c.decision_ts,
               c.accountability_due_ts, c.assignee,
               a.status, a.concluded_at, a.examiner, a.breached_policy,
               COALESCE(f.total, 0)    AS examined,
               COALESCE(f.adverse, 0)  AS implicated
          FROM analytics.fact_case c
          LEFT JOIN cases.staff_accountability a
                 ON a.tenant_id = c.tenant_id AND a.case_id = c.case_id
          LEFT JOIN (
                SELECT examination_id,
                       COUNT(*) AS total,
                       COUNT(*) FILTER (WHERE finding = ANY(:adverse)) AS adverse
                  FROM cases.accountability_findings
                 WHERE tenant_id = :t
                 GROUP BY examination_id
          ) f ON f.examination_id = a.id
         WHERE c.tenant_id = :t
           AND c.state IN ('fraud_declared', 'fmr_reported', 'closed_fraud')
         ORDER BY c.decision_ts DESC NULLS LAST
    """), {"t": tenant_id, "adverse": list(ADVERSE)}).mappings().all()

    now = datetime.now(timezone.utc)
    items = []
    for r in rows:
        due = _aware(r["accountability_due_ts"])
        status = r["status"] or ""
        concluded = status == "concluded"
        overdue = bool(due and not concluded and now > due)
        items.append({
            "case_id": r["case_id"], "state": r["state"],
            "amount_paise": r["amount_paise"], "decision_ts": r["decision_ts"],
            "due_ts": due, "status": status, "overdue": overdue,
            "concluded_at": r["concluded_at"], "examiner": r["examiner"] or "",
            "breached_policy": bool(r["breached_policy"]),
            "staff_examined": r["examined"], "staff_implicated": r["implicated"],
            "days_remaining": (int((due - now).total_seconds() // 86400)
                               if due and not concluded else None),
        })
    # Worst first: overdue, then never started, then in progress, then concluded.
    rank = {"": 1, "in_progress": 2, "concluded": 3}
    items.sort(key=lambda i: (0 if i["overdue"] else 1, rank.get(i["status"], 4),
                              i["due_ts"] or now))

    return {
        "window_days": pol.get("staff_accountability_days"),
        "count": len(items),
        "not_started": sum(1 for i in items if not i["status"]),
        "in_progress": sum(1 for i in items if i["status"] == "in_progress"),
        "concluded": sum(1 for i in items if i["status"] == "concluded"),
        "overdue": sum(1 for i in items if i["overdue"]),
        "concluded_late": sum(1 for i in items if i["breached_policy"]),
        "staff_implicated": sum(i["staff_implicated"] for i in items),
        "items": items,
    }
