"""Computing and reporting usage (BR-110).

The counting queries are dull. The rule around them is not: **a finalised day is never
recomputed.** Everything here exists to keep that true, because an invoice line a customer
queries has to come back with the same number it did last month.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from sqlalchemy import text
from sqlalchemy.orm import Session

from .usage_model import BY_KEY, METERS, UsageDaily

#: How the meters are counted. ``:t`` tenant, ``:d0``/``:d1`` the day's bounds.
#: Each reads only facts that accumulate, so a recomputation of an open day can move up
#: but never down - except seats, which is why seats is a high-water mark.
_QUERIES: dict[str, str] = {
    "transactions_ingested": """
        SELECT COUNT(*) FROM ingestion.raw_transaction
         WHERE tenant_id = :t AND received_at >= :d0 AND received_at < :d1
    """,
    "cbs_events_ingested": """
        SELECT COUNT(*) FROM ingestion.cbs_events
         WHERE tenant_id = :t AND received_at >= :d0 AND received_at < :d1
    """,
    "alerts_scored": """
        SELECT COUNT(*) FROM analytics.fact_alert
         WHERE tenant_id = :t AND ts >= :d0 AND ts < :d1
    """,
    "cases_opened": """
        SELECT COUNT(*) FROM analytics.fact_case
         WHERE tenant_id = :t AND opened_ts >= :d0 AND opened_ts < :d1
    """,
    # Seats is a *level*, not an event, so it cannot be counted over a window. The
    # current enabled-user count is taken and kept if it is higher than what the day has
    # already recorded - a user added and removed inside one day still used a seat.
    "seats": """
        SELECT COUNT(*) FROM tenant.tenant_users WHERE tenant_id = :t
    """,
}


def _bounds(day: date) -> tuple[datetime, datetime]:
    d0 = datetime(day.year, day.month, day.day, tzinfo=timezone.utc)
    return d0, d0 + timedelta(days=1)


def compute_day(db: Session, tenant_id: str, day: date, *,
                now: datetime | None = None) -> dict[str, int]:
    """Roll up one day. Refuses to touch a day that has already been finalised."""
    now = now or datetime.now(timezone.utc)
    today = now.date()
    if day > today:
        raise ValueError("cannot meter a day that has not happened")

    d0, d1 = _bounds(day)
    existing = {r.meter: r for r in db.query(UsageDaily).filter(
        UsageDaily.tenant_id == tenant_id, UsageDaily.day == day).all()}

    out: dict[str, int] = {}
    for meter in METERS:
        row = existing.get(meter.key)
        if row is not None and row.finalised:
            # Evidence, not a derived value. Left exactly as it was.
            out[meter.key] = row.quantity
            continue

        q = _QUERIES.get(meter.key)
        if q is None:
            continue
        try:
            value = int(db.execute(text(q), {"t": tenant_id, "d0": d0,
                                             "d1": d1}).scalar() or 0)
        except Exception:  # noqa: BLE001
            # A meter that cannot be read is left absent rather than recorded as zero:
            # a zero on an invoice line is a claim, and this one would be false.
            db.rollback()
            continue

        if meter.unit == "high_water" and row is not None:
            value = max(value, row.quantity)

        if row is None:
            row = UsageDaily(tenant_id=tenant_id, day=day, meter=meter.key,
                             quantity=value)
            db.add(row)
        else:
            row.quantity = value
            row.computed_at = now
        # The day is over, so this is the last time it will be computed.
        row.finalised = day < today
        out[meter.key] = value

    db.commit()
    return out


def backfill(db: Session, tenant_id: str, *, days: int = 30,
             now: datetime | None = None) -> dict[str, int]:
    """Roll up the last ``days`` days, skipping anything already finalised."""
    now = now or datetime.now(timezone.utc)
    totals: dict[str, int] = {}
    for i in range(days, -1, -1):
        day = (now - timedelta(days=i)).date()
        for k, v in compute_day(db, tenant_id, day, now=now).items():
            totals[k] = totals.get(k, 0) + (v if BY_KEY[k].unit != "high_water"
                                            else 0)
    return totals


def report(db: Session, tenant_id: str, start: date, end: date) -> dict:
    """Usage between two dates, inclusive. The shape an invoice is built from."""
    rows = db.execute(text("""
        SELECT day, meter, quantity, finalised
          FROM analytics.usage_daily
         WHERE tenant_id = :t AND day >= :s AND day <= :e
         ORDER BY day, meter
    """), {"t": tenant_id, "s": start, "e": end}).mappings().all()

    by_meter: dict[str, dict] = {}
    for m in METERS:
        by_meter[m.key] = {
            "meter": m.key, "label": m.label, "unit": m.unit, "basis": m.basis,
            "total": 0, "peak": 0, "days_counted": 0, "days_provisional": 0,
        }
    series: dict[str, list] = {m.key: [] for m in METERS}

    for r in rows:
        agg = by_meter.get(r["meter"])
        if agg is None:
            continue
        q = int(r["quantity"])
        if BY_KEY[r["meter"]].unit == "high_water":
            # A level is not summed across days; the billable figure is the peak.
            agg["peak"] = max(agg["peak"], q)
        else:
            agg["total"] += q
        agg["days_counted"] += 1
        if not r["finalised"]:
            agg["days_provisional"] += 1
        series[r["meter"]].append({"day": r["day"], "quantity": q,
                                   "finalised": bool(r["finalised"])})

    expected_days = (end - start).days + 1
    return {
        "from": start, "to": end, "days": expected_days,
        "meters": list(by_meter.values()),
        "series": series,
        # A period with missing days is not a cheap month - it is an unmetered one, and
        # billing from it would undercharge silently.
        "gaps": {m.key: expected_days - by_meter[m.key]["days_counted"]
                 for m in METERS},
        "complete": all(by_meter[m.key]["days_counted"] == expected_days
                        for m in METERS),
    }
