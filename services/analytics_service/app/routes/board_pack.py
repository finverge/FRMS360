"""Board / ACB pack endpoints (BR-507).

Generating is cheap and repeatable; **issuing is the governance act**. Once a pack is
issued it is frozen and every recipient is recorded individually, because "circulated to
the committee" is an assertion and a distribution row is evidence.
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
from cp_common.dynamic_roles import get_role

from ..board_pack import builder, renderers
from ..board_pack.sections import CADENCE_MONTHS
from ..board_pack_model import BoardPack, BoardPackDistribution
from ..engine import get_engine
from ..rules import policy

router = APIRouter(prefix="/analytics", tags=["board-pack"])

#: Who may prepare a pack. It aggregates the whole tenant's fraud position.
PREPARE_ROLES = ("risk_manager", "principal_officer", "supervisor", "tenant_admin", "cro")
#: Who may issue it to the committee. Narrower: this is the governance act.
ISSUE_ROLES = ("risk_manager", "principal_officer", "tenant_admin", "cro")


class GenerateIn(BaseModel):
    cadence: str = Field(default="", max_length=16)
    #: Any instant inside the period wanted. Defaults to the previous complete period,
    #: which is what a committee actually reviews - never the quarter still running.
    as_of: datetime | None = None


class IssueIn(BaseModel):
    recipients: list[str] = Field(default_factory=list)
    note: str = Field(default="", max_length=8000)


def _may(tenant_id: str, principal: Principal, roles: tuple[str, ...]) -> bool:
    return principal.role in roles or get_role(tenant_id, principal.role).can_admin_tenant


def _hash(payload: dict) -> str:
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()


def _entity(tenant_id: str) -> dict:
    pol = policy(tenant_id)
    out = {"entity_type": pol.get("entity_type"), "entity_label": pol.get("entity_label"),
           "governing_direction": pol.get("governing_direction"),
           "reporting_to": pol.get("reporting_to", [])}
    try:
        r = httpx.get(f"{settings.tenant_service_url}/tenants/internal/{tenant_id}/identity",
                      headers={"x-internal-key": settings.internal_api_key}, timeout=5.0)
        r.raise_for_status()
        t = r.json()
        out.update({"legal_name": t.get("legal_name"),
                    "display_name": t.get("display_name")})
    except Exception:  # noqa: BLE001
        # Left absent rather than filled with the tenant id: a pack naming the wrong
        # institution is worse than one that visibly could not name it.
        pass
    return out


def _cadence_for(tenant_id: str, requested: str) -> str:
    if requested:
        if requested not in CADENCE_MONTHS:
            raise AppError(
                f"Unknown cadence '{requested}'. One of: {', '.join(CADENCE_MONTHS)}",
                400, "unknown_cadence")
        return requested
    pol = policy(tenant_id)
    freq = str(pol["values"].get("board_review_frequency") or "quarterly")
    return freq if freq in CADENCE_MONTHS else "quarterly"


def _summary(row: BoardPack) -> dict:
    return {"id": row.id, "period_label": row.period_label, "cadence": row.cadence,
            "period_start": row.period_start, "period_end": row.period_end,
            "status": row.status, "revision": row.revision,
            "content_hash": row.content_hash, "generated_at": row.generated_at,
            "generated_by": row.generated_by, "issued_at": row.issued_at,
            "issued_by": row.issued_by, "note": row.note}


@router.post("/{tenant_id}/board-packs/generate")
def generate(
    tenant_id: str, payload: GenerateIn,
    db: Session = Depends(get_session),
    principal: Principal = Depends(get_current_principal),
) -> dict:
    resolve_tenant_scope(principal, tenant_id)
    if not _may(tenant_id, principal, PREPARE_ROLES):
        raise AppError("Your role may not prepare a board pack", 403,
                       "role_not_permitted")

    cadence = _cadence_for(tenant_id, payload.cadence)
    if payload.as_of:
        start, end, label = builder.period_for(payload.as_of, cadence)
    else:
        # The period that has actually finished. A committee reviewing the quarter it is
        # sitting in would be reading an incomplete number as if it were final.
        start, end, label = builder.previous_period(
            builder.period_for(datetime.now(timezone.utc), cadence)[0], cadence)

    pol = policy(tenant_id)
    data = builder.build(db, get_engine(db), tenant_id=tenant_id, cadence=cadence,
                         period_start=start, period_end=end, period_label=label,
                         entity=_entity(tenant_id), policy=pol["values"])

    prior = db.scalars(select(BoardPack).where(
        BoardPack.tenant_id == tenant_id, BoardPack.period_start == start,
        BoardPack.cadence == cadence)).all()
    # An unissued draft is superseded; an issued pack is left alone, because the
    # committee has already seen it and the minutes refer to it.
    for p in prior:
        if p.status == "draft":
            p.status = "superseded"

    row = BoardPack(
        tenant_id=tenant_id, period_label=label, period_start=start, period_end=end,
        cadence=cadence, payload=data, content_hash=_hash(data),
        revision=len(prior) + 1, generated_by=principal.subject)
    db.add(row)
    db.commit()

    record_audit(
        service="analytics-service", action="board_pack.generated",
        actor=principal.subject, actor_role=principal.role, tenant_id=tenant_id,
        target_type="board_pack", target_id=row.id, status="success",
        detail={"period": label, "cadence": cadence, "revision": row.revision,
                "hash": row.content_hash[:16]})
    return _summary(row)


@router.get("/{tenant_id}/board-packs")
def list_packs(
    tenant_id: str,
    db: Session = Depends(get_session),
    principal: Principal = Depends(get_current_principal),
) -> list[dict]:
    resolve_tenant_scope(principal, tenant_id)
    rows = db.scalars(select(BoardPack).where(
        BoardPack.tenant_id == tenant_id).order_by(
            BoardPack.period_start.desc(), BoardPack.generated_at.desc())).all()
    return [_summary(r) for r in rows]


@router.get("/{tenant_id}/board-packs/{pack_id}")
def get_pack(
    tenant_id: str, pack_id: str,
    db: Session = Depends(get_session),
    principal: Principal = Depends(get_current_principal),
) -> dict:
    resolve_tenant_scope(principal, tenant_id)
    row = db.scalars(select(BoardPack).where(
        BoardPack.tenant_id == tenant_id, BoardPack.id == pack_id)).first()
    if row is None:
        raise AppError("Board pack not found", 404, "not_found")
    dist = db.scalars(select(BoardPackDistribution).where(
        BoardPackDistribution.tenant_id == tenant_id,
        BoardPackDistribution.pack_id == pack_id)).all()
    return {**_summary(row), "payload": row.payload,
            "distributions": [{"recipient": d.recipient, "role": d.role,
                               "status": d.status, "detail": d.detail,
                               "sent_at": d.sent_at} for d in dist]}


@router.get("/{tenant_id}/board-packs/{pack_id}/download")
def download(
    tenant_id: str, pack_id: str,
    fmt: str = Query(default="html", pattern="^(html|json)$"),
    db: Session = Depends(get_session),
    principal: Principal = Depends(get_current_principal),
):
    """Render the stored payload. Never re-queries: this is what the committee saw."""
    resolve_tenant_scope(principal, tenant_id)
    row = db.scalars(select(BoardPack).where(
        BoardPack.tenant_id == tenant_id, BoardPack.id == pack_id)).first()
    if row is None:
        raise AppError("Board pack not found", 404, "not_found")

    record_audit(
        service="analytics-service", action="board_pack.downloaded",
        actor=principal.subject, actor_role=principal.role, tenant_id=tenant_id,
        target_type="board_pack", target_id=pack_id, status="success",
        detail={"format": fmt, "period": row.period_label})

    if fmt == "json":
        return Response(renderers.to_json(row.payload), media_type="application/json")
    return Response(renderers.to_html(row.payload, pack=_summary(row)),
                    media_type="text/html")


@router.post("/{tenant_id}/board-packs/{pack_id}/issue")
def issue(
    tenant_id: str, pack_id: str, payload: IssueIn,
    db: Session = Depends(get_session),
    principal: Principal = Depends(get_current_principal),
) -> dict:
    """Put the pack in front of the committee and record who received it."""
    resolve_tenant_scope(principal, tenant_id)
    if not _may(tenant_id, principal, ISSUE_ROLES):
        raise AppError(
            "Issuing a board pack requires a Fraud Risk Manager, Principal Officer, "
            "CRO or Tenant Administrator.", 403, "role_not_permitted")
    row = db.scalars(select(BoardPack).where(
        BoardPack.tenant_id == tenant_id, BoardPack.id == pack_id)).first()
    if row is None:
        raise AppError("Board pack not found", 404, "not_found")
    if row.status == "issued":
        raise AppError("This pack has already been issued", 409, "already_issued")
    if row.status == "superseded":
        raise AppError(
            "This draft was superseded by a newer generation and cannot be issued.",
            409, "superseded")

    recipients = [r.strip() for r in payload.recipients if r.strip()]
    if not recipients:
        # A pack issued to nobody is not a governance record, and silently accepting it
        # would let a bank believe its committee had been informed.
        raise AppError(
            "Name at least one recipient. A pack issued to nobody evidences nothing.",
            400, "no_recipients")

    now = datetime.now(timezone.utc)
    row.status = "issued"
    row.issued_by = principal.subject
    row.issued_at = now
    row.note = payload.note.strip()

    # Each recipient is notified through the notification service, which owns channel
    # configuration, per-user preferences and the retry ledger. Doing it here would mean
    # a second, unmonitored delivery path - and the first thing anyone would ask when a
    # pack did not arrive is which one to look in.
    summary = _pack_summary(row.payload)
    for r in recipients:
        status, detail = _notify_recipient(
            tenant_id, recipient=r, pack=row, summary=summary)
        db.add(BoardPackDistribution(
            tenant_id=tenant_id, pack_id=pack_id, recipient=r,
            status=status, detail=detail))
    db.commit()

    record_audit(
        service="analytics-service", action="board_pack.issued",
        actor=principal.subject, actor_role=principal.role, tenant_id=tenant_id,
        target_type="board_pack", target_id=pack_id, status="success",
        detail={"period": row.period_label, "recipients": len(recipients),
                "hash": row.content_hash[:16]})
    return get_pack(tenant_id, pack_id, db, principal)



def _pack_summary(payload: dict) -> dict:
    """The two figures a committee member wants before opening the attachment."""
    cases = overdue = 0
    for section in (payload or {}).get("sections", []):
        for m in section.get("metrics", []):
            if m.get("name") == "fraud_case_count" and m.get("value") is not None:
                cases = int(m["value"])
            if m.get("name") in ("fmr_overdue_count", "str_overdue_count")                     and m.get("value") is not None:
                overdue += int(m["value"])
    return {"cases": cases, "overdue": overdue}


def _notify_recipient(tenant_id: str, *, recipient: str, pack, summary: dict):
    """Hand one recipient to the notification service. Returns (status, detail).

    Failure is recorded against the distribution row rather than raised: the pack has
    already been issued and frozen, and unwinding a governance record because a mail
    relay was down would be the wrong trade. What must not happen is the row claiming
    delivery that did not occur.
    """
    try:
        resp = httpx.post(
            f"{settings.notification_service_url}/internal/notifications/raise",
            headers={"x-internal-key": settings.internal_api_key}, timeout=5.0,
            json={"tenant_id": tenant_id, "recipient": recipient,
                  "kind": "board_pack_issued",
                  # One notification per recipient per pack. Re-issuing is impossible, so
                  # this can never legitimately fire twice.
                  "dedupe_key": f"board_pack:{pack.id}:{recipient}",
                  "context": {"period": pack.period_label,
                              "issued_by": pack.issued_by,
                              "cases": summary["cases"],
                              "overdue": summary["overdue"]},
                  "link": f"/?board_pack={pack.id}"})
        resp.raise_for_status()
        body = resp.json()
        if body.get("created"):
            return "sent", "Notified via the notification service."
        # Already present: the dedupe key matched, so the recipient has it.
        return "sent", "Already notified for this pack."
    except Exception as exc:  # noqa: BLE001
        return "failed", (
            f"Could not notify: {type(exc).__name__}. The pack is issued and frozen; "
            f"this recipient has not been told.")[:2000]


@router.get("/{tenant_id}/board-packs/schedule/due")
def due(
    tenant_id: str,
    db: Session = Depends(get_session),
    principal: Principal = Depends(get_current_principal),
) -> dict:
    """Has the committee been given a pack for every completed period?

    The gap this answers is the one that matters: not "is a pack overdue today" but
    "which completed periods went by with nothing put in front of the board".
    """
    resolve_tenant_scope(principal, tenant_id)
    cadence = _cadence_for(tenant_id, "")
    now = datetime.now(timezone.utc)

    issued = {r.period_start: r for r in db.scalars(select(BoardPack).where(
        BoardPack.tenant_id == tenant_id, BoardPack.cadence == cadence)).all()}

    # Walk back over the last two years of completed periods.
    periods, cursor = [], builder.period_for(now, cadence)[0]
    for _ in range(int(24 / CADENCE_MONTHS[cadence]) + 1):
        start, end, label = builder.previous_period(cursor, cadence)
        pack = issued.get(start)
        periods.append({
            "period_label": label, "period_start": start, "period_end": end,
            "status": pack.status if pack else "missing",
            "pack_id": pack.id if pack else None,
            "issued_at": pack.issued_at if pack else None,
        })
        cursor = start

    return {"cadence": cadence, "count": len(periods),
            "missing": sum(1 for p in periods if p["status"] == "missing"),
            "draft_not_issued": sum(1 for p in periods if p["status"] == "draft"),
            "periods": periods,
            # So the console disables a control with its reason rather than offering a
            # button that fails on click (BR-410).
            "may_prepare": _may(tenant_id, principal, PREPARE_ROLES),
            "may_issue": _may(tenant_id, principal, ISSUE_ROLES),
            "issue_requires": ("Issuing requires a Fraud Risk Manager, Principal "
                               "Officer, CRO or Tenant Administrator.")}
