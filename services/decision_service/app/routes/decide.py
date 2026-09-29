"""POST /decide — the inline lane's only hot endpoint.

Everything on this path is a lookup or arithmetic. There is no aggregate query, no call
to another service, and no write before the response: the decision log is written after
the answer is produced, so persistence never sits inside the customer's payment window.
"""
from __future__ import annotations

import logging
import time
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from cp_common import AppError, Principal, get_session, require_machine_scope

from .. import counters, decide as decide_mod, evaluate as evaluate_mod, policy as policy_mod
from ..models import DecisionLog
from ..store import catalogue_for, frm_policy_for, policy_for

router = APIRouter(prefix="/decide", tags=["decision"])

log = logging.getLogger(__name__)

#: Decision-log writes that failed, since this process started. Read by /health and by the
#: shadow report, because a report drawn from an evidence base that is silently not being
#: written is worse than no report.
WRITE_FAILURES = {"count": 0, "last_error": "", "last_at": ""}

#: Log every failure up to this many, then one in every hundred. A permission error
#: repeats on every payment; the first few are the diagnosis and the rest are noise that
#: would drown the log the operator needs.
_LOG_FIRST = 5


def _note_write_failure(exc: Exception) -> None:
    WRITE_FAILURES["count"] += 1
    WRITE_FAILURES["last_error"] = str(exc)[:300]
    WRITE_FAILURES["last_at"] = datetime.now(timezone.utc).isoformat()
    n = WRITE_FAILURES["count"]
    if n <= _LOG_FIRST or n % 100 == 0:
        log.error(
            "decision log write failed (%d since start): %s. The decision was returned to "
            "the caller; it is the record that is missing. Shadow-mode reporting and any "
            "later 'why was this declined' question are both blind until this is fixed.",
            n, str(exc)[:300])


class DecideIn(BaseModel):
    """The payment to decide, plus whatever the channel already knows.

    Flags such as ``risk_signals`` and ``list_match_score`` are accepted from the caller
    because the channel has them in hand at authorisation time — asking this service to
    re-derive them would put a lookup on the path for data that already exists.
    """
    txn_ref: str = Field(max_length=64)
    rail: str = Field(max_length=8)
    debtor_account: str = Field(max_length=64)
    creditor_account: str = Field(default="", max_length=64)
    amount_paise: int = Field(ge=0)
    channel: str = Field(default="", max_length=16)
    device_id: str = Field(default="", max_length=64)
    #: Observations the channel supplies directly. Kept open rather than enumerated so a
    #: new request-sourced indicator needs no contract change.
    signals: dict[str, float] = Field(default_factory=dict)


class DecideOut(BaseModel):
    reference: str
    action: str
    outcome: str
    score: float
    severity: str
    enforced: bool
    would_be: str
    matched: list
    skipped: list
    took_ms: float
    budget_ms: int
    policy_version: str
    shadow: bool


@router.post("", response_model=DecideOut)
def decide_payment(
    payload: DecideIn,
    tenant_id: str,
    db: Session = Depends(get_session),
    principal: Principal = Depends(require_machine_scope("decide")),
) -> DecideOut:
    started = time.perf_counter()

    pol = policy_for(tenant_id)
    rp = pol.for_rail(payload.rail)
    if rp is None:
        # No policy for this rail is not an allow — it is a configuration error, and
        # answering "allow" would make an unconfigured rail look screened.
        raise AppError(
            f"No inline decision policy for rail '{payload.rail}'. Configure the rail, "
            f"or route it to the near-real-time lane explicitly.",
            409, "no_rail_policy")

    if rp.mode != policy_mod.MODE_INLINE:
        raise AppError(
            f"Rail '{payload.rail}' is configured for the near-real-time lane. It is "
            f"scored with full context after ingestion, not in the payment window.",
            409, "rail_not_inline")

    request = {
        "debtor_account": payload.debtor_account,
        "creditor_account": payload.creditor_account,
        "amount_paise": payload.amount_paise,
        **payload.signals,
    }

    store = counters.build("postgres", db)
    ev = evaluate_mod.evaluate(
        rules=catalogue_for(tenant_id), request=request, store=store,
        tenant_id=tenant_id, budget_ms=rp.budget_ms)

    total_ms = (time.perf_counter() - started) * 1000
    decision = decide_mod.decide(ev=ev, rp=rp, policy_version=pol.version,
                                 total_ms=total_ms,
                                 frm_policy=frm_policy_for(tenant_id))

    # Written after the answer is formed. A slow write must not become slow advice.
    try:
        db.add(DecisionLog(
            id=str(uuid.uuid4()), tenant_id=tenant_id, txn_ref=payload.txn_ref,
            rail=payload.rail.upper(), debtor_account=payload.debtor_account,
            creditor_account=payload.creditor_account,
            amount_paise=payload.amount_paise,
            action=decision.would_be, outcome=decision.outcome,
            enforced=decision.enforced, matched=decision.matched,
            skipped=decision.skipped, took_ms=decision.took_ms,
            lookup_ms=decision.lookup_ms, evaluate_ms=decision.evaluate_ms,
            budget_ms=decision.budget_ms, policy_version=decision.policy_version,
            score=decision.score, severity=decision.severity))
        db.commit()
    except Exception as exc:  # noqa: BLE001
        # The decision stands. Losing the log is bad; withholding an answer the payment
        # is waiting on is worse.
        db.rollback()
        # But it must not be silent. Swallowing this without a trace hid a real failure:
        # svc_decision had no grant on its own schema, so every decision this service ever
        # made went unrecorded while /decide kept returning 200. The lane looked healthy
        # and its entire evidence base was empty. One lost row is a blip; a persistent
        # failure means shadow mode is measuring nothing, and somebody has to be told.
        _note_write_failure(exc)

    return DecideOut(
        reference=decision.reference, action=decision.action,
        outcome=decision.outcome, score=decision.score,
        severity=decision.severity, enforced=decision.enforced,
        would_be=decision.would_be, matched=decision.matched,
        skipped=decision.skipped, took_ms=round(decision.took_ms, 3),
        budget_ms=decision.budget_ms, policy_version=decision.policy_version,
        shadow=rp.shadow)
