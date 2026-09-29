"""Per-tenant usage metering (BR-110).

Counting is the easy half. The property that makes a figure billable is that it does not
move: an invoice line a customer queries months later has to come back with the same
number. Most of these tests are about that.
"""
from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import text

from cp_common.db import SessionLocal
from services.analytics_service.app import usage_service as us
from services.analytics_service.app.usage_model import BY_KEY, METERS, UsageDaily


@pytest.fixture(autouse=True)
def _clean(tid):
    yield
    db = SessionLocal()
    try:
        db.execute(text("DELETE FROM analytics.usage_daily WHERE tenant_id = :t"),
                   {"t": tid})
        db.commit()
    finally:
        db.close()


def _compute(tid, day, now=None):
    db = SessionLocal()
    try:
        return us.compute_day(db, tid, day, now=now)
    finally:
        db.close()


def _rows(tid):
    db = SessionLocal()
    try:
        return {(r.day, r.meter): r for r in
                db.query(UsageDaily).filter(UsageDaily.tenant_id == tid).all()}
    finally:
        db.close()


# ------------------------------------------------------------------ the meters
def test_every_meter_states_how_it_is_counted():
    """The first thing a customer asks when they query an invoice line."""
    for m in METERS:
        assert m.basis, f"{m.key} does not say what it counts"
        assert m.unit in ("count", "high_water")


def test_a_day_is_rolled_up_for_every_meter(tid):
    today = datetime.now(timezone.utc).date()
    out = _compute(tid, today)
    assert set(out) == {m.key for m in METERS}


# ------------------------------------------------------------------ immutability
def test_a_closed_day_is_finalised(tid):
    now = datetime.now(timezone.utc)
    yesterday = (now - timedelta(days=1)).date()
    _compute(tid, yesterday, now=now)
    rows = _rows(tid)
    assert all(r.finalised for (d, _m), r in rows.items() if d == yesterday)


def test_today_is_provisional(tid):
    now = datetime.now(timezone.utc)
    _compute(tid, now.date(), now=now)
    rows = _rows(tid)
    assert not any(r.finalised for (d, _m), r in rows.items() if d == now.date())


def test_a_finalised_day_is_never_recomputed(tid):
    """The property an invoice rests on.

    Forces a wrong figure into a closed day, then reruns the rollup. If anything
    recomputes it the value moves - and an invoice built last month stops reconciling.
    """
    now = datetime.now(timezone.utc)
    yesterday = (now - timedelta(days=1)).date()
    _compute(tid, yesterday, now=now)

    db = SessionLocal()
    try:
        db.execute(text(
            "UPDATE analytics.usage_daily SET quantity = 999999 "
            "WHERE tenant_id = :t AND day = :d AND meter = 'alerts_scored'"),
            {"t": tid, "d": yesterday})
        db.commit()
    finally:
        db.close()

    out = _compute(tid, yesterday, now=now)
    assert out["alerts_scored"] == 999999, "a finalised figure was recomputed"


def test_an_open_day_is_recomputed(tid):
    now = datetime.now(timezone.utc)
    today = now.date()
    _compute(tid, today, now=now)
    db = SessionLocal()
    try:
        db.execute(text(
            "UPDATE analytics.usage_daily SET quantity = 424242 "
            "WHERE tenant_id = :t AND day = :d AND meter = 'alerts_scored'"),
            {"t": tid, "d": today})
        db.commit()
    finally:
        db.close()
    out = _compute(tid, today, now=now)
    assert out["alerts_scored"] != 424242, "today's figure was frozen too early"


def test_a_future_day_is_refused(tid):
    tomorrow = (datetime.now(timezone.utc) + timedelta(days=1)).date()
    with pytest.raises(ValueError):
        _compute(tid, tomorrow)


# ------------------------------------------------------------------ seats
def test_seats_is_a_high_water_mark_not_a_closing_count(tid):
    """A user added and removed inside one day still used a seat."""
    assert BY_KEY["seats"].unit == "high_water"
    now = datetime.now(timezone.utc)
    today = now.date()
    _compute(tid, today, now=now)

    db = SessionLocal()
    try:
        row = db.query(UsageDaily).filter(
            UsageDaily.tenant_id == tid, UsageDaily.day == today,
            UsageDaily.meter == "seats").one()
        peak = row.quantity + 5
        row.quantity = peak
        db.commit()
    finally:
        db.close()

    # Recomputing sees fewer users now, but the peak stands.
    out = _compute(tid, today, now=now)
    assert out["seats"] == peak, "the seat high-water mark was lowered"


def test_seats_is_not_summed_across_days(tid):
    """Summing a level would bill a ten-seat tenant for three hundred seats a month."""
    now = datetime.now(timezone.utc)
    days = [(now - timedelta(days=i)).date() for i in (2, 1, 0)]
    for d in days:
        _compute(tid, d, now=now)
    db = SessionLocal()
    try:
        rep = us.report(db, tid, days[0], days[-1])
    finally:
        db.close()
    seats = next(m for m in rep["meters"] if m["meter"] == "seats")
    assert seats["total"] == 0, "a level meter was summed"
    assert seats["peak"] > 0


# ------------------------------------------------------------------ reporting
def test_the_report_totals_events_and_peaks_levels(tid):
    now = datetime.now(timezone.utc)
    days = [(now - timedelta(days=i)).date() for i in (1, 0)]
    for d in days:
        _compute(tid, d, now=now)
    db = SessionLocal()
    try:
        rep = us.report(db, tid, days[0], days[-1])
    finally:
        db.close()
    assert rep["days"] == 2
    by = {m["meter"]: m for m in rep["meters"]}
    assert by["alerts_scored"]["days_counted"] == 2
    assert by["alerts_scored"]["unit"] == "count"
    assert by["seats"]["unit"] == "high_water"
    # Every meter carries its basis into the report.
    assert all(m["basis"] for m in rep["meters"])


def test_a_period_with_missing_days_is_reported_as_incomplete(tid):
    """A month with unmetered days is not a cheap month; billing from it undercharges."""
    now = datetime.now(timezone.utc)
    today = now.date()
    _compute(tid, today, now=now)
    db = SessionLocal()
    try:
        rep = us.report(db, tid, today - timedelta(days=6), today)
    finally:
        db.close()
    assert rep["complete"] is False
    assert rep["gaps"]["alerts_scored"] == 6


def test_provisional_days_are_flagged_in_the_report(tid):
    now = datetime.now(timezone.utc)
    today = now.date()
    _compute(tid, today, now=now)
    db = SessionLocal()
    try:
        rep = us.report(db, tid, today, today)
    finally:
        db.close()
    by = {m["meter"]: m for m in rep["meters"]}
    assert by["alerts_scored"]["days_provisional"] == 1


# ------------------------------------------------------------------ the endpoint
def test_usage_is_visible_to_a_tenant_admin(analytics_client, token_for, tid):
    r = analytics_client.get(f"/analytics/{tid}/usage",
                             headers=token_for("tenant_admin"))
    assert r.status_code == 200, r.text
    assert {m["meter"] for m in r.json()["meters"]} == {m.key for m in METERS}


def test_an_analyst_may_not_see_billing_usage(analytics_client, token_for, tid):
    r = analytics_client.get(f"/analytics/{tid}/usage", headers=token_for("analyst"))
    assert r.status_code == 403


def test_a_backwards_period_is_refused(analytics_client, token_for, tid):
    r = analytics_client.get(f"/analytics/{tid}/usage",
                             headers=token_for("tenant_admin"),
                             params={"date_from": "2026-08-01", "date_to": "2026-07-01"})
    assert r.status_code == 400


def test_the_meter_catalogue_is_readable(analytics_client, token_for, tid):
    body = analytics_client.get(f"/analytics/{tid}/usage/meters",
                                headers=token_for("tenant_admin")).json()
    assert len(body) == len(METERS)
    assert all(m["basis"] for m in body)


def test_usage_is_scoped_to_its_tenant(analytics_client, token_for, tid):
    r = analytics_client.get(
        "/analytics/00000000-0000-0000-0000-000000000000/usage",
        headers=token_for("tenant_admin"))
    assert r.status_code in (403, 404)
