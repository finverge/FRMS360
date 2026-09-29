"""Assemble a board pack for a period.

The pack is built from the metric registry and the case tables, never from bespoke SQL.
Where a figure cannot be produced it is reported *as unavailable, with the reason* rather
than defaulted to zero - a board reading "0 overdue returns" because a query failed is
worse informed than one reading "not available: metric errored".
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import text

from ..accountability_model import ADVERSE
from ..metrics import get_metric
from ..models import FRAUD_STATES
from ..schemas import FilterQuery
from .. import data_source
from .sections import CADENCE_MONTHS, SECTIONS


def period_for(when: datetime, cadence: str) -> tuple[datetime, datetime, str]:
    """The period *containing* ``when``, as (start, end, label).

    Indian financial year: 1 April to 31 March. A board pack labelled "Q1" that ran
    January to March would be quietly wrong in every set of minutes it appeared in.

    ``end`` is **exclusive** - the first instant of the next period. Every filter in the
    engine is ``ts < date_to``, so an inclusive 23:59:59 end would silently drop the last
    second of the quarter. Consecutive periods therefore abut exactly, with no gap for a
    transaction to fall into and no overlap for one to be counted twice.
    """
    months = CADENCE_MONTHS.get(cadence, 3)
    fy_start_year = when.year if when.month >= 4 else when.year - 1
    # Index of the month within the financial year: April = 0.
    fy_month = (when.month - 4) % 12
    block = fy_month // months
    start_fy_month = block * months
    start_month = ((4 - 1 + start_fy_month) % 12) + 1
    start_year = fy_start_year + (1 if start_fy_month >= 9 else 0)
    start = datetime(start_year, start_month, 1, tzinfo=timezone.utc)

    # The month after the period's last month, as an exclusive upper bound.
    next_fy_month = start_fy_month + months
    end_month = ((4 - 1 + next_fy_month) % 12) + 1
    end_year = fy_start_year + (1 if next_fy_month >= 9 else 0)
    end = datetime(end_year, end_month, 1, tzinfo=timezone.utc)

    fy = f"FY{fy_start_year}-{str(fy_start_year + 1)[-2:]}"
    if months == 3:
        label = f"Q{block + 1} {fy}"
    elif months == 12:
        label = fy
    elif months == 6:
        label = f"H{block + 1} {fy}"
    else:
        label = f"{start.strftime('%b %Y')}"
    return start, end, label


def _filters(period_start: datetime, period_end: datetime) -> FilterQuery:
    return FilterQuery(date_from=period_start, date_to=period_end)


def _metric(engine, name: str, f) -> dict:
    """One registry metric, or an honest failure."""
    try:
        r = engine.metric(name, f)
        return {"name": r.name, "label": r.label, "value": r.value, "unit": r.unit,
                "volatile": r.volatile, "available": True}
    except Exception as exc:  # noqa: BLE001
        m = None
        try:
            m = get_metric(name)
        except Exception:  # noqa: BLE001
            pass
        return {"name": name, "label": getattr(m, "label", name), "value": None,
                "unit": getattr(m, "unit", ""), "volatile": False,
                "available": False, "why": str(exc)[:200]}


def _accountability(db, tenant_id: str, start: datetime, end: datetime) -> dict:
    """The staff accountability position (BR-414), summarised for the committee."""
    row = db.execute(text("""
        SELECT COUNT(*)                                              AS declared,
               COUNT(a.id)                                           AS examined,
               COUNT(*) FILTER (WHERE a.status = 'concluded')         AS concluded,
               COUNT(*) FILTER (WHERE a.status = 'in_progress')       AS in_progress,
               COUNT(*) FILTER (WHERE a.id IS NULL)                   AS not_started,
               COUNT(*) FILTER (WHERE a.status IS DISTINCT FROM 'concluded'
                                  AND c.accountability_due_ts < now()) AS overdue,
               COUNT(*) FILTER (WHERE a.breached_policy)              AS concluded_late
          FROM analytics.fact_case c
          LEFT JOIN cases.staff_accountability a
                 ON a.tenant_id = c.tenant_id AND a.case_id = c.case_id
         WHERE c.tenant_id = :t
           AND c.state IN ('fraud_declared', 'fmr_reported', 'closed_fraud')
           AND c.opened_ts >= :s AND c.opened_ts < :e
    """), {"t": tenant_id, "s": start, "e": end}).mappings().first() or {}

    implicated = db.execute(text("""
        SELECT f.finding, COUNT(*) AS n
          FROM cases.accountability_findings f
          JOIN analytics.fact_case c
            ON c.tenant_id = f.tenant_id AND c.case_id = f.case_id
         WHERE f.tenant_id = :t
           AND c.opened_ts >= :s AND c.opened_ts < :e
           AND f.finding = ANY(:adverse)
         GROUP BY f.finding ORDER BY n DESC
    """), {"t": tenant_id, "s": start, "e": end,
           "adverse": list(ADVERSE)}).mappings().all()

    return {**{k: int(v or 0) for k, v in dict(row).items()},
            "adverse_findings": [dict(r) for r in implicated],
            "staff_implicated": sum(int(r["n"]) for r in implicated)}


def _material_cases(db, tenant_id: str, start: datetime, end: datetime,
                    floor_paise: int, limit: int = 50) -> list[dict]:
    """Cases the board's own policy says it must see by name.

    Restricted to states the registry counts as fraud. Without this the table listed
    *exonerated* cases under a heading the board reads as frauds - a large allegation
    that was investigated and not sustained would have been presented as a loss.
    """
    rows = db.execute(text("""
        SELECT case_id, state, amount_paise, recovered_paise, fmr_category,
               opened_ts, decision_ts, fmr_filed_ts, fmr_due_ts
          FROM analytics.fact_case
         WHERE tenant_id = :t AND amount_paise >= :floor
           AND state = ANY(:states)
           AND opened_ts >= :s AND opened_ts < :e
         ORDER BY amount_paise DESC
         LIMIT :lim
    """), {"t": tenant_id, "floor": floor_paise, "s": start, "e": end,
           "states": list(FRAUD_STATES), "lim": limit}).mappings().all()
    out = []
    for r in rows:
        d = dict(r)
        # Stated on the row rather than left for the reader to work out from two dates.
        d["reported_late"] = bool(r["fmr_due_ts"] and r["fmr_filed_ts"]
                                  and r["fmr_filed_ts"] > r["fmr_due_ts"])
        d["unreported"] = r["fmr_filed_ts"] is None
        # The payload lands in a JSONB column, which will not take a datetime. Converted
        # here rather than at write time so the stored pack and the API agree on shape.
        for k, v in list(d.items()):
            if isinstance(v, datetime):
                d[k] = v.isoformat()
        out.append(d)
    return out


def _dormant(db, tenant_id: str, start: datetime, end: datetime) -> dict:
    """Configured indicators that produced nothing. EWS coverage on paper only."""
    rows = db.execute(text("""
        SELECT rule_id, COUNT(*) AS n
          FROM analytics.fact_alert
         WHERE tenant_id = :t AND ts >= :s AND ts <= :e
         GROUP BY rule_id
    """), {"t": tenant_id, "s": start, "e": end}).mappings().all()
    fired = {r["rule_id"]: int(r["n"]) for r in rows}
    return {"fired": fired, "distinct_rules_fired": len(fired)}


def build(db, engine, *, tenant_id: str, cadence: str, period_start: datetime,
          period_end: datetime, period_label: str, entity: dict, policy: dict) -> dict:
    """The whole pack payload for one period."""
    f = _filters(period_start, period_end).to_filters(tenant_id)

    built_sections = []
    for s in SECTIONS:
        sec = {"key": s.key, "title": s.title, "why": s.why,
               "metrics": [_metric(engine, n, f) for n in s.metrics],
               "breakdowns": []}
        for metric_name, dim in s.breakdowns:
            try:
                m = get_metric(metric_name)
                sec["breakdowns"].append({
                    "metric": metric_name, "dimension": dim, "available": True,
                    # Carried from the registry so the renderer formats rupees as rupees
                    # without inferring the unit from the metric's name.
                    "label": m.label, "unit": m.unit,
                    "rows": engine.breakdown(metric_name, dim, f)})
            except Exception as exc:  # noqa: BLE001
                sec["breakdowns"].append({
                    "metric": metric_name, "dimension": dim, "available": False,
                    "rows": [], "why": str(exc)[:200]})

        if s.key == "accountability":
            sec["accountability"] = _accountability(db, tenant_id, period_start,
                                                    period_end)
        elif s.key == "materiality":
            floor = int(policy.get("board_reporting_paise", 0) or 0)
            sec["floor_paise"] = floor
            sec["cases"] = _material_cases(db, tenant_id, period_start, period_end, floor)
        elif s.key == "detection":
            sec["detection"] = _dormant(db, tenant_id, period_start, period_end)
        built_sections.append(sec)

    return {
        "schema_version": "1.0.0",
        "tenant_id": tenant_id,
        "entity": entity,
        "period": {"label": period_label, "cadence": cadence,
                   "start": period_start.isoformat(),
                   # Exclusive bound for querying; the inclusive last day for reading.
                   "end": period_end.isoformat(),
                   "end_inclusive": (period_end - timedelta(days=1)).isoformat(),
                   # Stated, not assumed. Every section - the registry metrics and the
                   # named case lists alike - selects cases by when the case was opened,
                   # which is what the dashboards do. Mixing in declaration date would
                   # have made the headline count and the named cases two different
                   # populations, and the reconciliation would happen in the meeting.
                   "basis": "Cases are counted by case opening date, consistently with "
                            "every dashboard figure."},
        "policy": {k: policy.get(k) for k in (
            "board_reporting_paise", "lea_referral_paise", "material_fraud_paise",
            "staff_accountability_days", "fmr_filing_days", "str_filing_days",
            "board_review_frequency")},
        "sections": built_sections,
        # What the pack is made of (BR-611). A board pack is not a regulatory submission,
        # so demonstration data is not refused outright the way it is for a return - a pack
        # is exactly what you show in a demo. It is stamped instead, and the renderer says
        # so on the face of the document, because a director reading a governance pack has
        # no other way to know the numbers are not the bank's.
        "source_mix": _source_mix(engine, f),
        "built_at": datetime.now(timezone.utc).isoformat(),
    }


def _source_mix(engine, f) -> dict:
    """Case counts per provenance, and whether anything non-live is in the pack."""
    try:
        rows = engine.breakdown("case_count", "source", f)
    except Exception:            # a pack must still build if the breakdown is unavailable
        return {"known": False, "has_non_live": False, "counts": {}}
    counts = {(r.get("key") or "unknown"): int(r.get("value") or 0) for r in rows}
    non_live = {k: v for k, v in counts.items()
                if not data_source.is_live(k) and v}
    return {
        "known": True,
        "counts": counts,
        "has_non_live": bool(non_live),
        "non_live": sorted(non_live),
        "note": ("This pack includes " +
                 ", ".join(f"{v} {data_source.describe(k).lower()} case(s)"
                           for k, v in sorted(non_live.items())) +
                 ". It is not a record of the bank's own fraud position."
                 ) if non_live else "",
    }


def previous_period(start: datetime, cadence: str) -> tuple[datetime, datetime, str]:
    """The period immediately before the one starting at ``start``."""
    return period_for(start - timedelta(days=1), cadence)
