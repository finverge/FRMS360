"""Reading back what Lane A decided — the half that makes shadow mode mean anything.

Shadow mode records the decision it *would* have enforced and returns allow. That is only
useful if somebody can look at the record. Until this module existed, ``would_be`` was
written faithfully and could not be read by anything: the decision service exposed no GET,
and analytics-service has no grant on the ``decision`` schema, deliberately. A tenant was
being asked to measure a false-positive cost with no way to measure it.

**This is a reporting surface, not the payment path.** It shares a process with ``/decide``
but nothing else: these queries aggregate over a time window and would never be acceptable
inside a payment window. They are separated here so that stays obvious, and so a slow
report is never mistaken for a slow decision.

**Absence is reported, never zeroed.** A rail with no decisions in the window is not a rail
with a 0% decline rate — it is a rail we cannot say anything about. The distinction decides
whether a bank enforces, so every figure here is either measured or explicitly absent.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from cp_common import AppError, Principal, get_current_principal, get_session
from cp_common.dynamic_roles import can_access_dashboard, can_access_module
from cp_common.rbac import MOD_MONITORING
from cp_common.tenancy import resolve_tenant_scope

from ..models import ACTIONS, OUTCOMES, DecisionLog
from .decide import WRITE_FAILURES
from ..store import policy_for

router = APIRouter(prefix="/decisions", tags=["decisions"])

#: Long enough to cover a full business cycle, short enough that the query stays a
#: reporting query. A tenant wanting more than this wants an export, not a dashboard.
MAX_WINDOW_DAYS = 90

#: Stand-in so a rail with no policy reads as "not inline" rather than raising.
_NO_RAIL = type("_NoRail", (), {"mode": "none"})()


def _guard(principal: Principal, tenant_id: str) -> str:
    tenant_id = resolve_tenant_scope(principal, tenant_id)
    if not can_access_module(tenant_id, principal.role, MOD_MONITORING):
        raise AppError("Your role has no access to monitoring", 403, "module_forbidden")
    if not can_access_dashboard(tenant_id, principal.role, "realtime"):
        # Aggregate-only roles (board, CRO) do not get payment-level records. Hiding the
        # tab is not access control; this is.
        raise AppError(
            f"Your role has no access to the real-time view. Inline decisions are "
            f"payment-level records, not an aggregate.", 403, "dashboard_forbidden")
    return tenant_id


def _window(days: int) -> tuple[datetime, datetime]:
    if days < 1 or days > MAX_WINDOW_DAYS:
        raise AppError(f"Window must be between 1 and {MAX_WINDOW_DAYS} days", 400,
                       "bad_window")
    now = datetime.now(timezone.utc)
    return now - timedelta(days=days), now


def _pct(part: int, whole: int) -> float | None:
    """None, not zero, when there is nothing to take a percentage of."""
    return round(100.0 * part / whole, 2) if whole else None


def _write_health() -> dict | None:
    """Whether this report is drawn from a complete record.

    A shadow report is an argument for or against enforcing, so it has to disclose when
    the thing it is counting was not reliably written. Process-local, so it under-reports
    across replicas — which is stated rather than glossed.
    """
    if not WRITE_FAILURES["count"]:
        return None
    return {
        "failed_writes_since_start": WRITE_FAILURES["count"],
        "last_error": WRITE_FAILURES["last_error"],
        "last_at": WRITE_FAILURES["last_at"],
        "meaning": "Decisions were returned to callers but not recorded. The figures "
                   "below undercount by an unknown amount and must not be used to "
                   "decide whether to enforce until this is fixed.",
        "scope": "This replica only; other replicas may have failed separately.",
    }


@router.get("/{tenant_id}/shadow")
def shadow_report(
    tenant_id: str,
    days: int = Query(default=7, ge=1, le=MAX_WINDOW_DAYS),
    rails: list[str] = Query(default=[]),
    db: Session = Depends(get_session),
    principal: Principal = Depends(get_current_principal),
) -> dict:
    """What enforcing Lane A would have cost, measured on the tenant's own traffic.

    This is the number a bank negotiates over before leaving shadow mode: not the latency,
    which is ours to guarantee, but how many of its genuine payments we would have stopped.
    """
    tenant_id = _guard(principal, tenant_id)
    since, until = _window(days)

    # A rail filter from the console applies to the whole report. Rails the tenant has
    # not put inline are named back rather than silently returning nothing: "NEFT has no
    # inline decisions" and "NEFT is not handled by this lane" are different answers, and
    # only the second one is true.
    wanted = [r.strip().upper() for r in rails if r.strip()]
    pol = policy_for(tenant_id)
    not_inline = [r for r in wanted
                  if pol is None or (pol.for_rail(r) or _NO_RAIL).mode != "inline"]

    def scope(q):
        q = q.where(DecisionLog.tenant_id == tenant_id, DecisionLog.decided_at >= since)
        return q.where(DecisionLog.rail.in_(wanted)) if wanted else q

    total = db.scalar(scope(select(func.count()).select_from(DecisionLog))) or 0

    if not total:
        # The honest empty state. Every field a caller might average or chart is absent
        # rather than zero, so a dashboard cannot render "0% declined" over no data.
        return {
            "tenant_id": tenant_id, "window_days": days,
            "since": since.isoformat(), "until": until.isoformat(),
            "total": 0, "measured": False,
            "why_not": "No inline decisions were recorded in this window. Either no rail "
                       "is configured for the inline lane, or no traffic reached it. "
                       "Nothing here can be inferred from that — it is not a clean run.",
            "by_action": {}, "by_outcome": {}, "by_severity": {}, "by_rail": [],
            "by_rule": [],
            "latency_ms": None, "enforcement": None,
            "record_health": _write_health(),
            "filtered_rails": wanted,
            "rails_not_inline": not_inline,
        }

    def counts(column) -> dict:
        rows = db.execute(scope(select(column, func.count())).group_by(column)).all()
        return {str(k): int(v) for k, v in rows}

    by_action = counts(DecisionLog.action)
    by_outcome = counts(DecisionLog.outcome)
    # Severity is an alert-level band over the whole match set, not a per-rule label -
    # one velocity hit is not critical, velocity with layering and structuring is. So it
    # is counted per decision here rather than inside the rule breakdown.
    by_severity = counts(DecisionLog.severity)

    # Every declared action and outcome appears, so a reader can tell "none of these
    # happened" from "this category does not exist".
    by_action = {a: by_action.get(a, 0) for a in ACTIONS}
    by_outcome = {o: by_outcome.get(o, 0) for o in OUTCOMES}

    # Per rail: the projected impact, in payments and in money.
    rail_rows = db.execute(
        scope(select(DecisionLog.rail, DecisionLog.action, func.count(),
                     func.coalesce(func.sum(DecisionLog.amount_paise), 0)))
        .group_by(DecisionLog.rail, DecisionLog.action)).all()

    rails: dict[str, dict] = {}
    for rail, action, n, paise in rail_rows:
        r = rails.setdefault(rail, {"rail": rail, "total": 0, "actions": {},
                                    "stopped": 0, "stopped_paise": 0})
        r["total"] += int(n)
        r["actions"][action] = int(n)
        if action in ("decline", "hold"):
            r["stopped"] += int(n)
            r["stopped_paise"] += int(paise)

    by_rail = []
    for r in sorted(rails.values(), key=lambda x: -x["total"]):
        rp = pol.for_rail(r["rail"]) if pol else None
        by_rail.append({
            **r,
            "stopped_pct": _pct(r["stopped"], r["total"]),
            # Whether this rail is still in shadow decides how the figure reads: a
            # projection of what would happen, or a record of what did.
            "shadow": None if rp is None else rp.shadow,
            "budget_ms": None if rp is None else rp.budget_ms,
        })

    # Which rules drive the declines. A single noisy rule is the usual reason a shadow
    # run looks unaffordable, and it is a threshold change rather than a reason to stop.
    rule_hits: dict[str, dict] = {}
    for (matched, action) in db.execute(
            scope(select(DecisionLog.matched, DecisionLog.action))
            .where(func.jsonb_array_length(DecisionLog.matched) > 0)).all():
        for m in matched or []:
            rid = str(m.get("rule_id") or "unknown")
            e = rule_hits.setdefault(rid, {"rule_id": rid, "family": m.get("family", ""),
                                           "matches": 0, "led_to_stop": 0})
            e["matches"] += 1
            if action in ("decline", "hold"):
                e["led_to_stop"] += 1
    by_rule = sorted(rule_hits.values(), key=lambda x: -x["matches"])

    lat = db.execute(scope(
        select(func.percentile_disc(0.5).within_group(DecisionLog.took_ms),
               func.percentile_disc(0.95).within_group(DecisionLog.took_ms),
               func.percentile_disc(0.99).within_group(DecisionLog.took_ms),
               func.max(DecisionLog.took_ms)))).one()

    enforced = db.scalar(scope(select(func.count()).select_from(DecisionLog))
                         .where(DecisionLog.enforced.is_(True))) or 0

    stopped = by_action.get("decline", 0) + by_action.get("hold", 0)
    return {
        "tenant_id": tenant_id, "window_days": days,
        "since": since.isoformat(), "until": until.isoformat(),
        "total": total, "measured": True,
        "record_health": _write_health(),
        "filtered_rails": wanted,
        "rails_not_inline": not_inline,
        "by_action": by_action,
        "by_outcome": by_outcome,
        "by_severity": by_severity,
        "by_rail": by_rail,
        "by_rule": by_rule,
        "latency_ms": {"p50": lat[0], "p95": lat[1], "p99": lat[2], "max": lat[3]},
        "enforcement": {
            "enforced": enforced,
            "shadow": total - enforced,
            "shadow_pct": _pct(total - enforced, total),
        },
        # The headline. Stated as "would have been stopped" because in shadow mode that
        # is exactly what it is - these payments all completed.
        "projected_impact": {
            "would_stop": stopped,
            "would_stop_pct": _pct(stopped, total),
            "note": ("These payments completed. The figure is what enforcing the current "
                     "policy would have stopped, on this traffic, at these thresholds."
                     if enforced < total else
                     "Some decisions in this window were enforced; the figure mixes "
                     "payments actually stopped with ones that would have been."),
        },
    }


@router.get("/{tenant_id}/recent")
def recent_decisions(
    tenant_id: str,
    days: int = Query(default=7, ge=1, le=MAX_WINDOW_DAYS),
    action: str = Query(default=""),
    rail: str = Query(default=""),
    limit: int = Query(default=100, ge=1, le=500),
    db: Session = Depends(get_session),
    principal: Principal = Depends(get_current_principal),
) -> dict:
    """The individual decisions behind the aggregate, newest first."""
    tenant_id = _guard(principal, tenant_id)
    since, _ = _window(days)

    q = select(DecisionLog).where(DecisionLog.tenant_id == tenant_id,
                                  DecisionLog.decided_at >= since)
    if action:
        if action not in ACTIONS:
            raise AppError(f"'{action}' is not a decision action", 400, "bad_action")
        q = q.where(DecisionLog.action == action)
    if rail:
        q = q.where(DecisionLog.rail == rail.upper())

    rows = db.scalars(q.order_by(DecisionLog.decided_at.desc()).limit(limit)).all()
    return {
        "tenant_id": tenant_id, "count": len(rows), "limit": limit,
        "truncated": len(rows) == limit,
        "decisions": [{
            "id": d.id, "txn_ref": d.txn_ref, "rail": d.rail,
            "amount_paise": d.amount_paise,
            "would_be": d.action, "enforced": d.enforced, "outcome": d.outcome,
            "matched": d.matched, "skipped": d.skipped,
            "took_ms": round(d.took_ms, 2), "budget_ms": d.budget_ms,
            "policy_version": d.policy_version,
            "decided_at": d.decided_at.isoformat() if d.decided_at else None,
        } for d in rows],
    }


@router.get("/{tenant_id}/by-reference/{txn_ref}")
def decision_for_payment(
    tenant_id: str,
    txn_ref: str,
    db: Session = Depends(get_session),
    principal: Principal = Depends(get_current_principal),
) -> dict:
    """Why was this payment decided the way it was?

    The question a branch is asked months later, by a customer or an inspection. It is
    answered from the record rather than reconstructed, because the thresholds and the
    catalogue will have moved on — which is why the policy version is on every row.
    """
    tenant_id = _guard(principal, tenant_id)
    rows = db.scalars(
        select(DecisionLog).where(DecisionLog.tenant_id == tenant_id,
                                  DecisionLog.txn_ref == txn_ref)
        .order_by(DecisionLog.decided_at.desc())).all()
    if not rows:
        raise AppError(
            f"No inline decision is recorded for '{txn_ref}'. That is not the same as an "
            f"approval: the rail may be handled by the near-real-time lane, or the "
            f"reference may belong to a different tenant.", 404, "no_decision")
    return {
        "tenant_id": tenant_id, "txn_ref": txn_ref, "attempts": len(rows),
        "decisions": [{
            "id": d.id, "rail": d.rail, "amount_paise": d.amount_paise,
            "would_be": d.action,
            "enforced": d.enforced,
            "returned_to_channel": d.action if d.enforced else "allow",
            "outcome": d.outcome,
            "matched": d.matched, "skipped": d.skipped,
            "took_ms": round(d.took_ms, 2), "budget_ms": d.budget_ms,
            "policy_version": d.policy_version,
            "decided_at": d.decided_at.isoformat() if d.decided_at else None,
            "error": d.error or None,
        } for d in rows],
    }
