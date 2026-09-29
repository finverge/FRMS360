"""Dashboard endpoints, one per persona view.

These handlers contain **no SQL**. They select metric names from the registry and hand
them to the engine. That is what keeps the three dashboards reconciled: the board's
"total fraud value" and the supervisor's are the same registry entry, not two queries
that happen to look alike.
"""
from dataclasses import replace
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from cp_common import (
    AppError,
    Principal,
    get_current_principal,
    get_session,
    record_audit,
    resolve_tenant_scope,
)
from cp_common.dynamic_roles import can_access_dashboard, can_access_module, can_reveal_pii
from cp_common.rbac import MOD_MONITORING

from .. import data_source
from .. import reconciliation
from ..engine import get_engine
from fastapi.responses import PlainTextResponse

from .. import ai_insights
from .. import drift as drift_mod
from .. import governance as gov_mod
from .. import export as export_mod
from .. import geolocation
from .. import ofac
from .. import privacy
from .. import rules as rules_bridge
from ..dpdp_client import mask_rows_via_dpdp
from ..detection.reference import availability as reference_availability
from ..metrics import catalogue
from ..schemas import (
    AiInsightsOut, DashboardOut, FilterQuery, GeolocateOut, MetricOut, MuleRiskIndicatorsOut,
    OfacScreenOut,
)

router = APIRouter(prefix="/analytics", tags=["analytics"])


def _filters(
    tenant_id: str,
    date_from: datetime | None = Query(default=None),
    date_to: datetime | None = Query(default=None),
    rails: list[str] = Query(default=[]),
    families: list[str] = Query(default=[]),
    severities: list[str] = Query(default=[]),
    regions: list[str] = Query(default=[]),
    products: list[str] = Query(default=[]),
    segments: list[str] = Query(default=[]),
    states: list[str] = Query(default=[]),
    fmr_categories: list[str] = Query(default=[]),
    dispositions: list[str] = Query(default=[]),
    min_amount_paise: int | None = Query(default=None),
    max_amount_paise: int | None = Query(default=None),
    account: str | None = Query(default=None),
    id_search: str | None = Query(default=None),
    branches: list[str] = Query(default=[]),
    rules: list[str] = Query(default=[]),
    typologies: list[str] = Query(default=[]),
    analysts: list[str] = Query(default=[]),
    config_versions: list[str] = Query(default=[]),
    assignees: list[str] = Query(default=[]),
    rfa_only: bool = Query(default=False),
    fmr_status: str | None = Query(default=None),
    str_status: str | None = Query(default=None),
    nj_breach_only: bool = Query(default=False),
    sources: list[str] = Query(default=[]),
):
    return FilterQuery(
        date_from=date_from, date_to=date_to, rails=rails, families=families,
        severities=severities, regions=regions, products=products, segments=segments,
        states=states, fmr_categories=fmr_categories, dispositions=dispositions,
        min_amount_paise=min_amount_paise, max_amount_paise=max_amount_paise,
        account=account, id_search=id_search, branches=branches, rules=rules, typologies=typologies,
        analysts=analysts, config_versions=config_versions, assignees=assignees,
        rfa_only=rfa_only, fmr_status=fmr_status, str_status=str_status,
        nj_breach_only=nj_breach_only, sources=sources,
    ).to_filters(tenant_id)


def _guard(principal: Principal, tenant_id: str, dashboard: str | None = None) -> None:
    """Tenant scope + module access + (optionally) dashboard access.

    Hiding a tab in the UI is not access control; this is where it is actually enforced.
    """
    resolve_tenant_scope(principal, tenant_id)
    if not can_access_module(tenant_id, principal.role, MOD_MONITORING):
        raise AppError("Your role has no access to monitoring", 403, "module_forbidden")
    if dashboard and not can_access_dashboard(tenant_id, principal.role, dashboard):
        raise AppError(
            f"Your role has no access to the {dashboard} dashboard", 403, "dashboard_forbidden"
        )


def _resolve_reveal(principal: Principal, tenant_id: str, reveal: bool,
                    justification: str | None, context: str) -> bool:
    """Decide whether PII may be unmasked, and record it when it is.

    Three conditions, all required: the role holds the capability, the caller asked
    explicitly, and a justification was supplied. Anything less returns masked data.
    """
    if not reveal:
        return False
    if not can_reveal_pii(tenant_id, principal.role):
        raise AppError(
            "Your role may not unmask customer data", 403, "pii_reveal_forbidden")
    if not justification or len(justification.strip()) < 8:
        raise AppError(
            "A justification (min 8 chars) is required to unmask customer data",
            400, "justification_required")
    record_audit(
        service="analytics-service", action="data.reveal_pii", actor=principal.subject,
        actor_role=principal.role, tenant_id=tenant_id, target_type="dataset",
        target_id=context, status="success",
        detail={"justification": justification.strip(), "context": context},
    )
    return True


def _metrics(engine, names: list[str], f) -> dict[str, MetricOut]:
    out = {}
    for n in names:
        r = engine.metric(n, f)
        out[n] = MetricOut(name=r.name, label=r.label, value=r.value, unit=r.unit,
                           volatile=r.volatile)
    return out


@router.get("/{tenant_id}/catalogue")
def metric_catalogue(
    tenant_id: str, principal: Principal = Depends(get_current_principal)
) -> list[dict]:
    _guard(principal, tenant_id)
    return catalogue()


@router.get("/{tenant_id}/data-sources")
def data_sources(
    tenant_id: str,
    f=Depends(_filters),
    db: Session = Depends(get_session),
    principal: Principal = Depends(get_current_principal),
) -> dict:
    """What the current filter set is actually made of (BR-611).

    A separate call rather than a field on every dashboard: it is cheap, the console
    needs it once per filter change rather than once per panel, and putting it on
    thirteen payloads would have meant thirteen chances to forget it.

    The point is disclosure. A demonstration corpus and live traffic legitimately share
    an instance - during a pilot they always do - and the failure mode is not that the
    demo data exists, it is quoting a figure without knowing what went into it.
    """
    _guard(principal, tenant_id)
    engine = get_engine(db)

    counts: dict[str, dict[str, int]] = {}
    for entity, metric_name in (("transaction", "transaction_count"),
                                ("alert", "alert_count"),
                                ("case", "case_count")):
        for row in engine.breakdown(metric_name, "source", f):
            key = (row.get("key") or "").strip().lower() or "unknown"
            counts.setdefault(key, {})[entity] = int(row.get("value") or 0)

    rows = [{"key": k,
             "label": data_source.describe(k),
             "live": data_source.is_live(k),
             "transactions": v.get("transaction", 0),
             "alerts": v.get("alert", 0),
             "cases": v.get("case", 0)}
            for k, v in sorted(counts.items(),
                               key=lambda kv: (not data_source.is_live(kv[0]), kv[0]))]

    non_live = [r for r in rows if not r["live"]]
    return {
        "filters": f.as_dict(),
        "sources": rows,
        # True when the figures on screen are a blend. The console turns this into a
        # banner, because a number that mixes real and demonstration data must never be
        # quoted without the reader knowing.
        "mixed": bool(non_live) and any(r["live"] for r in rows),
        "has_non_live": bool(non_live),
        "non_live_keys": [r["key"] for r in non_live],
    }


# ---------------- ANALYST ----------------
@router.get("/{tenant_id}/analyst", response_model=DashboardOut)
def analyst_dashboard(
    tenant_id: str,
    limit: int = Query(default=50, le=500),
    db: Session = Depends(get_session),
    principal: Principal = Depends(get_current_principal),
    f=Depends(_filters),
) -> DashboardOut:
    _guard(principal, tenant_id, "analyst")
    e = get_engine(db)
    return DashboardOut(
        dashboard="analyst", persona="L1 Fraud Analyst",
        generated_at=datetime.now(timezone.utc), filters=f.as_dict(),
        metrics=_metrics(e, [
            "alert_count", "pending_alert_count", "true_positive_count",
            "false_positive_count", "precision", "mtta_seconds", "mtti_seconds",
        ], f),
        breakdowns={
            "by_family": e.breakdown("alert_count", "family", f),
            "by_severity": e.breakdown("alert_count", "severity", f),
            "by_disposition": e.breakdown("alert_count", "disposition", f),
            "by_rail": e.breakdown("alert_count", "rail", f),
        },
        rows=privacy.mask_rows(e.rows("alert", f, limit=limit, offset=0), False),
    )


# ---------------- D-02 EWS SIGNAL MONITORING (P1) ----------------
@router.get("/{tenant_id}/ews", response_model=DashboardOut)
def ews_dashboard(
    tenant_id: str,
    db: Session = Depends(get_session),
    principal: Principal = Depends(get_current_principal),
    f=Depends(_filters),
) -> DashboardOut:
    """Which EWS indicators are firing, how well, and which are silent."""
    _guard(principal, tenant_id, "ews")
    e = get_engine(db)
    return DashboardOut(
        dashboard="ews", persona="Fraud Risk Manager / Analyst",
        generated_at=datetime.now(timezone.utc), filters=f.as_dict(),
        metrics=_metrics(e, [
            "alert_count", "active_rule_count", "active_family_count", "typology_count",
            "precision", "critical_alert_count", "layering_alert_count", "avg_score",
            # The 30-day examination window - see policy.py's ews_examination_days.
            # Originally set from the 2024 Directions; RBI withdrew those on 31 July
            # 2026 and replaced them with nine entity-specific successors that carry
            # the same chapter structure forward (see config_service/app/policy.py's
            # module docstring). On this dashboard rather than the compliance one
            # because the people who clear alerts are the people who need to see the
            # clock running.
            "ews_examination_overdue", "ews_examined_late_count",
            "ews_examination_p95_days",
        ], f),
        breakdowns={
            "by_family": e.breakdown("alert_count", "family", f),
            "by_rule": e.breakdown("alert_count", "rule", f),
            "precision_by_family": e.breakdown("precision", "family", f),
            "by_typology": e.breakdown("alert_count", "typology", f),
            "by_severity": e.breakdown("alert_count", "severity", f),
        },
        rows=privacy.mask_rows(e.rows("alert", f, limit=50, offset=0), False),
        # Which configured indicators produced nothing - invisible in any alert chart,
        # and precisely what an inspection asks about.
        dormant=rules_bridge.dormant_report(
            tenant_id, {r["key"] for r in e.breakdown("alert_count", "rule", f)},
            reference_availability(db, tenant_id)),
    )


# ---------------- D-04 RFA CASE LIFECYCLE (P1) ----------------
@router.get("/{tenant_id}/rfa", response_model=DashboardOut)
def rfa_dashboard(
    tenant_id: str,
    db: Session = Depends(get_session),
    principal: Principal = Depends(get_current_principal),
    f=Depends(_filters),
) -> DashboardOut:
    """Red-Flagged Account lifecycle and the natural-justice clock."""
    _guard(principal, tenant_id, "rfa")
    e = get_engine(db)
    return DashboardOut(
        dashboard="rfa", persona="Investigator / Compliance",
        generated_at=datetime.now(timezone.utc), filters=f.as_dict(),
        metrics=_metrics(e, [
            "rfa_count", "rfa_awaiting_show_cause", "nj_open_count", "nj_breach_count",
            "fraud_without_reasoned_order", "decided_case_count", "stalled_case_count",
            "open_case_count", "fraud_case_count", "oldest_open_case_days",
        ], f),
        breakdowns={
            "cases_by_state": e.breakdown("case_count", "state", f),
            "by_fmr_category": e.breakdown("case_count", "fmr_category", f),
            "by_severity": e.breakdown("case_count", "severity", f),
            "cases_by_assignee": e.breakdown("case_count", "assignee", f),
        },
        rows=privacy.mask_rows(e.rows("case", f, limit=100, offset=0), False),
    )


# ---------------- D-06 REAL-TIME TRANSACTION MONITORING (P2) ----------------
@router.get("/{tenant_id}/realtime", response_model=DashboardOut)
def realtime_dashboard(
    tenant_id: str,
    db: Session = Depends(get_session),
    principal: Principal = Depends(get_current_principal),
    f=Depends(_filters),
) -> DashboardOut:
    """Flow and interdiction: what is moving and what is being stopped."""
    _guard(principal, tenant_id, "realtime")
    e = get_engine(db)
    return DashboardOut(
        dashboard="realtime", persona="Operations",
        generated_at=datetime.now(timezone.utc), filters=f.as_dict(),
        metrics=_metrics(e, [
            "transaction_count", "throughput_value", "settled_count", "interdicted_count",
            "interdiction_rate", "prevented_value", "alert_count", "ingestion_lag_seconds",
        ], f),
        breakdowns={
            "by_rail": e.breakdown("transaction_count", "rail", f),
            "value_by_rail": e.breakdown("throughput_value", "rail", f),
            "by_status": e.breakdown("transaction_count", "status", f),
            "by_region": e.breakdown("transaction_count", "region", f),
            "by_product": e.breakdown("transaction_count", "product", f),
        },
        rows=privacy.mask_rows(e.rows("transaction", f, limit=50, offset=0), False),
    )


# ---------------- D-08 CUSTOMER / ACCOUNT 360 (P2) ----------------
@router.get("/{tenant_id}/account360", response_model=DashboardOut)
def account360_dashboard(
    tenant_id: str,
    db: Session = Depends(get_session),
    principal: Principal = Depends(get_current_principal),
    f=Depends(_filters),
) -> DashboardOut:
    """Everything known about one account. Pass ?account=AC... to pin it; without an
    account it shows the population so an investigator can pick one from the rows."""
    _guard(principal, tenant_id, "account360")
    e = get_engine(db)
    return DashboardOut(
        dashboard="account360", persona="Senior Investigator",
        generated_at=datetime.now(timezone.utc), filters=f.as_dict(),
        metrics=_metrics(e, [
            "transaction_count", "throughput_value", "alert_count", "critical_alert_count",
            "counterparty_count", "distinct_device_count", "linked_account_count",
            "interdicted_count",
        ], f),
        breakdowns={
            "by_rail": e.breakdown("transaction_count", "rail", f),
            "by_family": e.breakdown("alert_count", "family", f),
            "by_status": e.breakdown("transaction_count", "status", f),
            "by_product": e.breakdown("transaction_count", "product", f),
        },
        rows=privacy.mask_rows(e.rows("transaction", f, limit=100, offset=0), False),
    )


# ---------------- D-11 TENANT HEALTH (P3, platform staff only) ----------------
@router.get("/{tenant_id}/tenant_health", response_model=DashboardOut)
def tenant_health_dashboard(
    tenant_id: str,
    db: Session = Depends(get_session),
    principal: Principal = Depends(get_current_principal),
    f=Depends(_filters),
) -> DashboardOut:
    """Operational telemetry for a tenant's pipeline - deliberately no customer detail."""
    _guard(principal, tenant_id, "tenant_health")
    e = get_engine(db)
    return DashboardOut(
        dashboard="tenant_health", persona="Platform Administrator",
        generated_at=datetime.now(timezone.utc), filters=f.as_dict(),
        metrics=_metrics(e, [
            "ingestion_lag_seconds", "transaction_count", "alert_count", "case_count",
            "open_case_count", "oldest_open_case_days", "config_version_count",
            "active_rule_count",
        ], f),
        breakdowns={
            "by_rail": e.breakdown("transaction_count", "rail", f),
            "by_config_version": e.breakdown("alert_count", "config_version", f),
            "by_status": e.breakdown("transaction_count", "status", f),
            "cases_by_state": e.breakdown("case_count", "state", f),
        },
        # No rows: platform staff get operational telemetry, never customer records.
    )


# ---------------- SENIOR INVESTIGATOR (L2) ----------------
@router.get("/{tenant_id}/investigator", response_model=DashboardOut)
def investigator_dashboard(
    tenant_id: str,
    limit: int = Query(default=50, le=500),
    db: Session = Depends(get_session),
    principal: Principal = Depends(get_current_principal),
    f=Depends(_filters),
) -> DashboardOut:
    """Case deep-dive: ring size, layering signals and the accounts involved."""
    _guard(principal, tenant_id, "investigator")
    e = get_engine(db)
    return DashboardOut(
        dashboard="investigator", persona="L2 Senior Investigator",
        generated_at=datetime.now(timezone.utc), filters=f.as_dict(),
        metrics=_metrics(e, [
            "open_case_count", "rfa_count", "layering_alert_count", "critical_alert_count",
            "alerts_per_case", "linked_account_count", "counterparty_count",
            "true_positive_count",
        ], f),
        breakdowns={
            "cases_by_state": e.breakdown("case_count", "state", f),
            "by_family": e.breakdown("alert_count", "family", f),
            "by_typology": e.breakdown("alert_count", "typology", f),
            "by_region": e.breakdown("case_count", "region", f),
        },
        rows=privacy.mask_rows(e.rows("case", f, limit=limit, offset=0), False),
    )


# ---------------- FRAUD RISK MANAGER ----------------
@router.get("/{tenant_id}/risk_manager", response_model=DashboardOut)
def risk_manager_dashboard(
    tenant_id: str,
    db: Session = Depends(get_session),
    principal: Principal = Depends(get_current_principal),
    f=Depends(_filters),
) -> DashboardOut:
    """Operational control: queue health, SLA and analyst workload."""
    _guard(principal, tenant_id, "risk_manager")
    e = get_engine(db)
    return DashboardOut(
        dashboard="risk_manager", persona="Fraud Risk Manager",
        generated_at=datetime.now(timezone.utc), filters=f.as_dict(),
        metrics=_metrics(e, [
            "alert_count", "pending_alert_count", "untouched_alert_count",
            "sla_breach_count", "backlog_age_p95_hours", "mtta_seconds",
            "mtti_seconds", "precision", "open_case_count",
        ], f),
        breakdowns={
            "by_analyst": e.breakdown("alert_count", "analyst", f),
            "by_severity": e.breakdown("alert_count", "severity", f),
            "by_disposition": e.breakdown("alert_count", "disposition", f),
            "by_rail": e.breakdown("alert_count", "rail", f),
            "cases_by_assignee": e.breakdown("case_count", "assignee", f),
        },
        rows=privacy.mask_rows(e.rows("alert", f, limit=50, offset=0), False),
    )


# ---------------- PRINCIPAL OFFICER (PMLA / FIU-IND) ----------------
@router.get("/{tenant_id}/aml", response_model=DashboardOut)
def aml_dashboard(
    tenant_id: str,
    db: Session = Depends(get_session),
    principal: Principal = Depends(get_current_principal),
    f=Depends(_filters),
) -> DashboardOut:
    """PMLA obligation, tracked independently of the fraud-classification track."""
    _guard(principal, tenant_id, "aml")
    e = get_engine(db)
    return DashboardOut(
        dashboard="aml", persona="Principal Officer (PMLA)",
        generated_at=datetime.now(timezone.utc), filters=f.as_dict(),
        metrics=_metrics(e, [
            "str_due_count", "str_filed_count", "str_pending_count", "str_overdue_count",
            "str_filed_value", "layering_alert_count", "case_count", "rfa_count",
        ], f),
        breakdowns={
            "by_typology": e.breakdown("alert_count", "typology", f),
            "by_family": e.breakdown("alert_count", "family", f),
            "cases_by_state": e.breakdown("case_count", "state", f),
            "by_rail": e.breakdown("case_count", "rail", f),
        },
        rows=privacy.mask_rows(e.rows("case", f, limit=80, offset=0), False),
    )


# ---------------- MODEL RISK / DATA SCIENCE (FREE-AI) ----------------
@router.get("/{tenant_id}/model", response_model=DashboardOut)
def model_dashboard(
    tenant_id: str,
    db: Session = Depends(get_session),
    principal: Principal = Depends(get_current_principal),
    f=Depends(_filters),
) -> DashboardOut:
    """Model fitness and explainability evidence, per FREE-AI expectations."""
    _guard(principal, tenant_id, "model")
    e = get_engine(db)
    return DashboardOut(
        dashboard="model", persona="Model Risk / Data Science",
        generated_at=datetime.now(timezone.utc), filters=f.as_dict(),
        metrics=_metrics(e, [
            "alert_count", "dispositioned_count", "true_positive_count",
            "false_positive_count", "precision", "avg_score", "score_p95",
            "config_version_count",
        ], f),
        breakdowns={
            "by_config_version": e.breakdown("alert_count", "config_version", f),
            "by_rule": e.breakdown("alert_count", "rule", f),
            "by_family": e.breakdown("precision", "family", f),
            "by_severity": e.breakdown("alert_count", "severity", f),
            "by_segment": e.breakdown("alert_count", "segment", f),
        },
        rows=privacy.mask_rows(e.rows("alert", f, limit=50, offset=0), False),
    )


# ---------------- INTERNAL AUDIT / RBI INSPECTION ----------------
@router.get("/{tenant_id}/inspection", response_model=DashboardOut)
def inspection_dashboard(
    tenant_id: str,
    db: Session = Depends(get_session),
    principal: Principal = Depends(get_current_principal),
    f=Depends(_filters),
) -> DashboardOut:
    """Read-only assurance view: compliance clocks, evidence and live reconciliation."""
    _guard(principal, tenant_id, "inspection")
    e = get_engine(db)
    recon = reconciliation.run_all(e, f)
    recon["checked_at"] = datetime.now(timezone.utc).isoformat()
    return DashboardOut(
        dashboard="inspection", persona="Internal Audit / RBI Inspection",
        generated_at=datetime.now(timezone.utc), filters=f.as_dict(),
        metrics=_metrics(e, [
            "fraud_value_total", "fraud_case_count", "rfa_count",
            "nj_open_count", "nj_breach_count",
            "fmr_due_count", "fmr_filed_count", "fmr_overdue_count",
            "str_due_count", "str_filed_count", "str_overdue_count",
            "config_version_count",
        ], f),
        breakdowns={
            "cases_by_state": e.breakdown("case_count", "state", f),
            "by_fmr_category": e.breakdown("fraud_value_total", "fmr_category", f),
            "by_config_version": e.breakdown("alert_count", "config_version", f),
        },
        rows=privacy.mask_rows(e.rows("case", f, limit=100, offset=0), False),
        reconciliation=recon,
    )


# ---------------- BOARD ----------------
@router.get("/{tenant_id}/board", response_model=DashboardOut)
def board_dashboard(
    tenant_id: str,
    db: Session = Depends(get_session),
    principal: Principal = Depends(get_current_principal),
    f=Depends(_filters),
) -> DashboardOut:
    _guard(principal, tenant_id, "board")
    e = get_engine(db)
    return DashboardOut(
        dashboard="board", persona="CRO / Audit Committee",
        generated_at=datetime.now(timezone.utc), filters=f.as_dict(),
        metrics=_metrics(e, [
            "fraud_value_total", "fraud_case_count", "recovered_value", "net_fraud_value",
            "prevented_value", "throughput_value", "rfa_count", "case_count",
            "fmr_filed_count", "fmr_overdue_count",
        ], f),
        breakdowns={
            "by_fmr_category": e.breakdown("fraud_value_total", "fmr_category", f),
            "by_rail": e.breakdown("fraud_value_total", "rail", f),
            "by_region": e.breakdown("fraud_value_total", "region", f),
            "by_product": e.breakdown("fraud_value_total", "product", f),
            "cases_by_state": e.breakdown("case_count", "state", f),
        },
    )


# ---------------- SUPERVISOR ----------------
@router.get("/{tenant_id}/supervisor", response_model=DashboardOut)
def supervisor_dashboard(
    tenant_id: str,
    db: Session = Depends(get_session),
    principal: Principal = Depends(get_current_principal),
    f=Depends(_filters),
) -> DashboardOut:
    """Supervisory / inspection view: compliance clocks plus the live reconciliation
    result, so the figures are presented together with the proof that they tie."""
    _guard(principal, tenant_id, "supervisor")
    e = get_engine(db)
    recon = reconciliation.run_all(e, f)
    recon["checked_at"] = datetime.now(timezone.utc).isoformat()
    return DashboardOut(
        dashboard="supervisor", persona="Compliance / RBI Inspection",
        generated_at=datetime.now(timezone.utc), filters=f.as_dict(),
        metrics=_metrics(e, [
            "rfa_count", "nj_open_count", "nj_breach_count",
            "fmr_due_count", "fmr_filed_count", "fmr_overdue_count", "fmr_filed_value",
            "str_due_count", "str_filed_count", "str_overdue_count",
            "fraud_value_total", "open_case_count",
        ], f),
        breakdowns={
            "cases_by_state": e.breakdown("case_count", "state", f),
            "by_fmr_category": e.breakdown("fraud_value_total", "fmr_category", f),
        },
        rows=privacy.mask_rows(e.rows("case", f, limit=100, offset=0), False),
        reconciliation=recon,
    )


# ---------------- reconciliation (standalone) ----------------
@router.get("/{tenant_id}/reconciliation")
def reconciliation_report(
    tenant_id: str,
    db: Session = Depends(get_session),
    principal: Principal = Depends(get_current_principal),
    f=Depends(_filters),
) -> dict:
    _guard(principal, tenant_id, "supervisor")
    out = reconciliation.run_all(get_engine(db), f)
    out["checked_at"] = datetime.now(timezone.utc).isoformat()
    return out


# ---------------- generic breakdown (drives chart drill-down) ----------------
@router.get("/{tenant_id}/breakdown")
def breakdown(
    tenant_id: str,
    metric: str = Query(..., description="metric name from the registry"),
    dimension: str = Query(..., description="rail | region | product | segment | family | "
                                            "severity | disposition | state | fmr_category | rule"),
    db: Session = Depends(get_session),
    principal: Principal = Depends(get_current_principal),
    f=Depends(_filters),
) -> dict:
    """One metric grouped by one dimension — the unit of a drillable chart."""
    _guard(principal, tenant_id)
    try:
        rows = get_engine(db).breakdown(metric, dimension, f)
    except KeyError as exc:
        raise AppError(str(exc), 400, "bad_breakdown")
    return {"metric": metric, "dimension": dimension, "filters": f.as_dict(), "rows": rows}


@router.get("/{tenant_id}/dormant-rules")
def dormant_rules(
    tenant_id: str,
    db: Session = Depends(get_session),
    principal: Principal = Depends(get_current_principal),
    f=Depends(_filters),
) -> dict:
    """Configured EWS indicators that fired nothing in this window."""
    _guard(principal, tenant_id)
    fired = {r["key"] for r in get_engine(db).breakdown("alert_count", "rule", f)}
    return rules_bridge.dormant_report(tenant_id, fired,
                                       reference_availability(db, tenant_id))


@router.get("/{tenant_id}/export/{entity}", response_class=PlainTextResponse)
def export_rows(
    tenant_id: str,
    entity: str,
    limit: int = Query(default=5000, le=export_mod.MAX_ROWS),
    reveal: bool = Query(default=False),
    justification: str | None = Query(default=None),
    db: Session = Depends(get_session),
    principal: Principal = Depends(get_current_principal),
    f=Depends(_filters),
):
    """Filtered rows as watermarked CSV.

    An export leaves the platform, so it is masked by default, row-capped, carries its
    own provenance header, and is audited as an event in its own right.
    """
    _guard(principal, tenant_id)
    if entity not in ("alert", "case", "transaction"):
        raise AppError("entity must be alert | case | transaction", 400, "bad_entity")

    unmask = _resolve_reveal(principal, tenant_id, reveal, justification,
                             f"export:{entity}")
    engine = get_engine(db)
    total = engine.count(entity, f)
    # BRD P360-04/05 — the DPDP Compliance Platform's Data Minimization
    # Engine is the masking authority for this export path (falls back to
    # this service's own local privacy.mask_rows if the platform is
    # unreachable, never to unmasked data — see dpdp_client.py).
    rows = mask_rows_via_dpdp(tenant_id, engine.rows(entity, f, limit=limit, offset=0), unmask, principal.subject)

    record_audit(
        service="analytics-service", action="data.export", actor=principal.subject,
        actor_role=principal.role, tenant_id=tenant_id, target_type=entity,
        target_id=f"{len(rows)}-rows", status="success",
        detail={"rows": len(rows), "matching": total, "pii_revealed": unmask,
                "filters": {k: v for k, v in f.as_dict().items()
                            if v not in (None, [], "", False)}},
    )
    body = export_mod.to_csv(
        rows, tenant=tenant_id, entity=entity, actor=principal.subject,
        role=principal.role, filters=f.as_dict(), total=total, masked=not unmask)
    return PlainTextResponse(
        content=body, media_type="text/csv",
        headers={"Content-Disposition":
                 f'attachment; filename="{export_mod.filename(tenant_id[:8], entity)}"'},
    )


@router.get("/{tenant_id}/drift")
def model_drift(
    tenant_id: str,
    reference_days: int = Query(default=30, ge=1, le=365,
                                description="length of the reference window"),
    bins: int = Query(default=10, ge=4, le=25),
    db: Session = Depends(get_session),
    principal: Principal = Depends(get_current_principal),
    f=Depends(_filters),
) -> dict:
    """Score-distribution drift between the previous window and the current one.

    Precision alone cannot show that the population a model scores has moved underneath
    it, which is what FREE-AI expects to be monitored across a model's life.
    """
    _guard(principal, tenant_id, "model")
    engine = get_engine(db)

    now = datetime.now(timezone.utc)
    current_from = f.date_from or (now - timedelta(days=reference_days))
    current_to = f.date_to or now
    span = current_to - current_from

    current_f = replace(f, date_from=current_from, date_to=current_to)
    reference_f = replace(f, date_from=current_from - span, date_to=current_from)

    reference = engine.score_sample(reference_f)
    current = engine.score_sample(current_f)

    out = drift_mod.psi(reference, current, bins=bins)
    out["ks"] = drift_mod.ks(reference, current)
    out["windows"] = {
        "reference": {"from": (current_from - span).isoformat(),
                      "to": current_from.isoformat(), "alerts": len(reference)},
        "current": {"from": current_from.isoformat(),
                    "to": current_to.isoformat(), "alerts": len(current)},
    }
    # Which rules moved most - PSI says the population shifted, this says where to look.
    out["mix_shift"] = _mix_shift(engine, reference_f, current_f)
    return out


def _mix_shift(engine, reference_f, current_f, top: int = 8) -> list[dict]:
    """Change in each rule's share of total alerts between the two windows."""
    def share(filters):
        rows = engine.breakdown("alert_count", "rule", filters)
        total = sum(r["value"] for r in rows) or 1
        return {r["key"]: r["value"] / total for r in rows}

    ref, cur = share(reference_f), share(current_f)
    moved = [
        {"rule_id": rid,
         "reference_pct": round(ref.get(rid, 0) * 100, 2),
         "current_pct": round(cur.get(rid, 0) * 100, 2),
         "delta_pct": round((cur.get(rid, 0) - ref.get(rid, 0)) * 100, 2)}
        for rid in set(ref) | set(cur)
    ]
    moved.sort(key=lambda x: -abs(x["delta_pct"]))
    return moved[:top]


@router.get("/{tenant_id}/model-inventory")
def model_inventory(
    tenant_id: str,
    db: Session = Depends(get_session),
    principal: Principal = Depends(get_current_principal),
) -> dict:
    """What is actually scoring traffic, and whether the control plane approved it.

    Derived from what ran rather than a maintained list, because a spreadsheet inventory
    drifts from production and the drift is the risk.
    """
    _guard(principal, tenant_id, "model")
    approved = rules_bridge.approved_config_versions(tenant_id)
    return gov_mod.inventory(get_engine(db), tenant_id, approved)


@router.get("/{tenant_id}/overrides")
def override_log(
    tenant_id: str,
    limit: int = Query(default=200, le=1000),
    db: Session = Depends(get_session),
    principal: Principal = Depends(get_current_principal),
) -> dict:
    """Where a human disagreed with the model.

    Neither direction is wrong - that is what human accountability means - but a rising
    override rate says the model and its users have diverged, which precision hides.
    """
    _guard(principal, tenant_id, "model")
    return gov_mod.overrides(get_engine(db), tenant_id, limit=limit)


# ---------------- network / link analysis ----------------
@router.get("/{tenant_id}/graph")
def graph(
    tenant_id: str,
    case_id: str | None = Query(default=None),
    account: str | None = Query(default=None),
    limit: int = Query(default=300, le=1000),
    reveal: bool = Query(default=False),
    justification: str | None = Query(default=None),
    db: Session = Depends(get_session),
    principal: Principal = Depends(get_current_principal),
    f=Depends(_filters),
) -> dict:
    """The account graph behind a case or an account - how a ring is actually shaped.

    Node ids are customer account numbers, so the same masking rules apply here as
    anywhere else: masked unless the caller may reveal and says why.
    """
    _guard(principal, tenant_id)
    unmask = _resolve_reveal(principal, tenant_id, reveal, justification,
                             f"graph:{case_id or account or 'top-layering'}")
    g = get_engine(db).network(tenant_id, f, case_id=case_id, account=account, limit=limit)
    if not unmask:
        # Masking keeps a prefix and the last four digits, so two different accounts can
        # collapse to the same label. In a table that is harmless; in a graph it merges
        # two real accounts into one node and corrupts the structure. Build a stable
        # pseudonym per distinct account, disambiguating collisions.
        pseudonym: dict[str, str] = {}
        used: set[str] = set()
        for node in g["nodes"]:
            label = privacy.mask_value("account", node["id"])
            if label in used:
                label = f"{label}#{len(used)}"
            used.add(label)
            pseudonym[node["id"]] = label
        g["nodes"] = [{**n, "id": pseudonym[n["id"]]} for n in g["nodes"]]
        g["edges"] = [{**e,
                       "source": pseudonym.get(e["source"],
                                               privacy.mask_value("account", e["source"])),
                       "target": pseudonym.get(e["target"],
                                               privacy.mask_value("account", e["target"]))}
                      for e in g["edges"]]
    g["pii_masked"] = not unmask
    return g


@router.get("/{tenant_id}/mule-risk/{account}", response_model=MuleRiskIndicatorsOut)
def mule_risk_indicators(
    tenant_id: str,
    account: str,
    db: Session = Depends(get_session),
    principal: Principal = Depends(get_current_principal),
) -> MuleRiskIndicatorsOut:
    """Behavioural/network/velocity signals for one account, over 1D/1W/1M.

    The response is aggregate counts and sums only - no raw device id, IP, or
    counterparty account number ever leaves this endpoint - so unlike /graph or
    /evidence there is no reveal/justification gate here: there is nothing in the
    payload that masking would apply to.
    """
    _guard(principal, tenant_id)
    return get_engine(db).mule_risk_indicators(tenant_id, account)


@router.get("/{tenant_id}/ofac-screen", response_model=OfacScreenOut)
def ofac_screen(
    tenant_id: str,
    name: str = Query(..., min_length=1, max_length=350),
    db: Session = Depends(get_session),
    principal: Principal = Depends(get_current_principal),
) -> OfacScreenOut:
    """Fuzzy-screen a typed name against the real OFAC SDN list.

    Investigator-driven, not automatic: Fraud360's transaction/alert/case rows carry
    account numbers, not customer names, so there is nothing to screen without a name
    typed in from outside the record (a case note, a KYC document, ...). See ofac.py.
    """
    _guard(principal, tenant_id)
    result = ofac.screen(db, name)
    record_audit(
        service="analytics-service", action="ofac.screen", actor=principal.subject,
        actor_role=principal.role, tenant_id=tenant_id, target_type="ofac_sdn_list",
        target_id=name, status="success",
        detail={"match_count": len(result["matches"])},
    )
    return OfacScreenOut(**result, list_refreshed_at=ofac.last_refreshed(db))


@router.post("/{tenant_id}/geolocate/{txn_id}", response_model=GeolocateOut)
def geolocate_transaction(
    tenant_id: str,
    txn_id: str,
    reveal: bool = Query(default=False, description="unmask PII (needs justification)"),
    justification: str | None = Query(default=None),
    db: Session = Depends(get_session),
    principal: Principal = Depends(get_current_principal),
) -> GeolocateOut:
    """Resolve a transaction's real IP address to an approximate location - see
    geolocation.py. There is no lat/long in the data model, so this always needs the
    unmasked IP (the reveal gate is not optional here, unlike ai-insights)."""
    _guard(principal, tenant_id)
    unmask = _resolve_reveal(principal, tenant_id, reveal, justification, f"geolocate:transaction:{txn_id}")
    if not unmask:
        raise AppError(
            "Locating this transaction needs the real IP address - reveal PII first",
            400, "reveal_required",
        )
    payload = get_engine(db).evidence(tenant_id, "transaction", txn_id)
    if payload.get("error"):
        raise AppError("Record not found", 404, "not_found")
    ip = payload.get("transaction", {}).get("ip_addr")
    if not ip:
        raise AppError("This transaction has no IP address on record", 404, "no_ip")

    try:
        result = geolocation.locate_ip(str(ip))
    except geolocation.GeolocateUnavailableError as exc:
        raise AppError(str(exc), 503, "geolocation_unavailable") from exc

    record_audit(
        service="analytics-service", action="geolocate.transaction", actor=principal.subject,
        actor_role=principal.role, tenant_id=tenant_id, target_type="transaction", target_id=txn_id,
        status="success", detail={"locatable": result["locatable"]},
    )
    return GeolocateOut(**result)


@router.post("/{tenant_id}/ai-insights/{entity}/{ident}", response_model=AiInsightsOut)
def ai_insights_route(
    tenant_id: str,
    entity: str,
    ident: str,
    reveal: bool = Query(default=False, description="unmask PII (needs justification)"),
    justification: str | None = Query(default=None),
    db: Session = Depends(get_session),
    principal: Principal = Depends(get_current_principal),
) -> AiInsightsOut:
    """An LLM's read of one record's evidence - see ai_insights.py.

    A real, locally self-hosted model (Ollama/vLLM, never a paid hosted API - see
    LLM_BASE_URL) generates this on request; it is not pre-computed, cached, or
    fabricated if the model host is unreachable. The model sees exactly the same
    masked/unmasked payload the caller's own reveal grant entitles them to - same gate
    as GET .../evidence/{entity}/{ident}.
    """
    _guard(principal, tenant_id)
    if entity not in ("alert", "case", "transaction"):
        raise AppError("entity must be alert | case | transaction", 400, "bad_entity")
    unmask = _resolve_reveal(principal, tenant_id, reveal, justification, f"ai-insights:{entity}:{ident}")
    payload = get_engine(db).evidence(tenant_id, entity, ident)
    if payload.get("error"):
        raise AppError("Record not found", 404, "not_found")
    payload = privacy.mask_payload(payload, unmask)

    try:
        result = ai_insights.generate(payload, entity, ident)
    except ai_insights.LLMUnavailableError as exc:
        raise AppError(str(exc), 503, "llm_unavailable") from exc
    except ai_insights.LLMResponseError as exc:
        raise AppError(str(exc), 502, "llm_bad_response") from exc

    record_audit(
        service="analytics-service", action="ai_insights.generate", actor=principal.subject,
        actor_role=principal.role, tenant_id=tenant_id, target_type=entity, target_id=ident,
        status="success", detail={"decision": result["decision"], "risk_score": result["risk_score"],
                                   "model": result["model"], "pii_revealed": unmask},
    )
    return AiInsightsOut(entity=entity, id=ident, **result)


# ---------------- time series (trend) ----------------
@router.get("/{tenant_id}/timeseries")
def timeseries(
    tenant_id: str,
    metric: str = Query(..., description="metric name from the registry"),
    bucket: str = Query(default="day", description="day | week | month"),
    db: Session = Depends(get_session),
    principal: Principal = Depends(get_current_principal),
    f=Depends(_filters),
) -> dict:
    """A metric over time. Board oversight is about direction, not a single number."""
    _guard(principal, tenant_id)
    try:
        rows = get_engine(db).timeseries(metric, bucket, f)
    except KeyError as exc:
        raise AppError(str(exc), 400, "bad_timeseries")
    return {"metric": metric, "bucket": bucket, "filters": f.as_dict(), "rows": rows}


# ---------------- evidence (stage 6) ----------------
@router.get("/{tenant_id}/evidence/{entity}/{ident}")
def evidence(
    tenant_id: str,
    entity: str,
    ident: str,
    reveal: bool = Query(default=False),
    justification: str | None = Query(default=None),
    db: Session = Depends(get_session),
    principal: Principal = Depends(get_current_principal),
) -> dict:
    """Terminal drill stage: a single record with the trail that justifies it."""
    _guard(principal, tenant_id)
    if entity not in ("alert", "case", "transaction"):
        raise AppError("entity must be alert | case | transaction", 400, "bad_entity")
    unmask = _resolve_reveal(principal, tenant_id, reveal, justification,
                             f"evidence:{entity}:{ident}")
    payload = get_engine(db).evidence(tenant_id, entity, ident)
    # Audit-of-view: evidence is access to ONE identifiable customer record, so the fact
    # of looking is itself auditable - not just changes.
    record_audit(
        service="analytics-service", action="data.view_evidence", actor=principal.subject,
        actor_role=principal.role, tenant_id=tenant_id, target_type=entity,
        target_id=ident, status="success",
        detail={"pii_revealed": unmask, "pii_fields": privacy.fields_present(payload)},
    )
    return privacy.mask_payload(payload, unmask)


# ---------------- drill-down ----------------
@router.get("/{tenant_id}/drill/{entity}")
def drill(
    tenant_id: str,
    entity: str,
    limit: int = Query(default=50, le=1000),
    offset: int = Query(default=0, ge=0),
    reveal: bool = Query(default=False, description="unmask PII (needs justification)"),
    justification: str | None = Query(default=None),
    db: Session = Depends(get_session),
    principal: Principal = Depends(get_current_principal),
    f=Depends(_filters),
) -> dict:
    """Filter-preserving drill to alert / case / transaction rows."""
    _guard(principal, tenant_id)
    if entity not in ("alert", "case", "transaction"):
        raise AppError("entity must be alert | case | transaction", 400, "bad_entity")
    unmask = _resolve_reveal(principal, tenant_id, reveal, justification, f"drill:{entity}")
    engine = get_engine(db)
    total = engine.count(entity, f)
    rows = engine.rows(entity, f, limit=limit, offset=offset)
    return {
        "entity": entity, "filters": f.as_dict(),
        "count": len(rows), "total": total,
        "limit": limit, "offset": offset,
        "has_more": offset + len(rows) < total,
        "pii_masked": not unmask,
        # BRD P360-04/05 — same DPDP-platform-first, local-fallback rule as
        # export_rows above.
        "rows": mask_rows_via_dpdp(tenant_id, rows, unmask, principal.subject),
    }
