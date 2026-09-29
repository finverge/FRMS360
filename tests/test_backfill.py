"""Backfill and replay.

The line these tests hold: a simulation must create nothing at all, and a replay must add
only what was genuinely missing without ever touching work a human has done.
"""
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text

from cp_common.db import SessionLocal
from services.analytics_service.app.detection import backfill


def _counts(tid):
    db = SessionLocal()
    try:
        return {
            "alerts": db.scalar(text("SELECT COUNT(*) FROM analytics.fact_alert "
                                     "WHERE tenant_id = :t").bindparams(t=tid)),
            "cases": db.scalar(text("SELECT COUNT(*) FROM analytics.fact_case "
                                    "WHERE tenant_id = :t").bindparams(t=tid)),
        }
    finally:
        db.close()


def _window(tid):
    """A window that actually contains transactions."""
    db = SessionLocal()
    try:
        row = db.execute(text(
            "SELECT MIN(ts), MAX(ts) FROM analytics.fact_transaction "
            "WHERE tenant_id = :t"), {"t": tid}).first()
    finally:
        db.close()
    lo, hi = row
    if lo is None:
        pytest.skip("no transactions for this tenant")
    lo = lo if lo.tzinfo else lo.replace(tzinfo=timezone.utc)
    hi = hi if hi.tzinfo else hi.replace(tzinfo=timezone.utc)
    hi = min(hi + timedelta(seconds=1), lo + timedelta(days=backfill.MAX_WINDOW_DAYS - 1))
    return lo, hi


def _post(client, token_for, tid, lo, hi, role="risk_manager", **body):
    return client.post(f"/analytics/{tid}/detection/backfill", headers=token_for(role),
                       json={"window_from": lo.isoformat(), "window_to": hi.isoformat(),
                             **body})


# -------------------------------------------------------------- simulation writes nothing
def test_a_simulation_creates_absolutely_nothing(analytics_client, token_for, tid):
    """The property the whole feature depends on. A "what if" that leaves alerts behind
    is not a what-if, and nobody would ever trust it again."""
    lo, hi = _window(tid)
    before = _counts(tid)
    r = _post(analytics_client, token_for, tid, lo, hi)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["mode"] == "simulate"
    assert body["written"] == 0
    assert _counts(tid) == before, "a simulation changed the database"


def test_a_simulation_reports_what_would_fire_against_what_did(analytics_client,
                                                               token_for, tid):
    lo, hi = _window(tid)
    body = _post(analytics_client, token_for, tid, lo, hi).json()
    assert body["transactions"] > 0
    assert isinstance(body["would_fire"], dict)
    assert isinstance(body["actual"], dict)
    # added/removed must be consistent with the two sides they are derived from.
    for rid, n in body["added"].items():
        assert n == body["would_fire"].get(rid, 0) - body["actual"].get(rid, 0)
    for rid, n in body["removed"].items():
        assert n == body["actual"].get(rid, 0) - body["would_fire"].get(rid, 0)


def test_a_threshold_override_changes_the_simulated_outcome(analytics_client, token_for,
                                                            tid):
    """The question a bank actually asks before retuning a band.

    Compared at the two extremes rather than against the configured default. Real
    thresholds often sit in a flat part of the distribution - moving LAY-02 from 20 to 15
    changed nothing on live data - so a nearby value proves nothing about whether the
    override is wired up at all.
    """
    lo, hi = _window(tid)
    before = _counts(tid)
    loose = _post(analytics_client, token_for, tid, lo, hi,
                  thresholds={"LAY-02": 1}).json()
    strict = _post(analytics_client, token_for, tid, lo, hi,
                   thresholds={"LAY-02": 1_000_000}).json()

    assert strict["would_fire"].get("LAY-02", 0) == 0, (
        "an unreachable threshold still fired - the override is not being applied")
    assert loose["would_fire"].get("LAY-02", 0) > 0, (
        "a threshold of 1 fired nothing - the override is not being applied")
    assert loose["written"] == strict["written"] == 0
    assert _counts(tid) == before, "a simulation with overrides changed the database"


def test_an_override_naming_an_unknown_rule_is_refused(analytics_client, token_for, tid):
    lo, hi = _window(tid)
    r = _post(analytics_client, token_for, tid, lo, hi, thresholds={"NOPE-99": 1})
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "unknown_rule"


def test_overrides_are_refused_on_a_replay(analytics_client, token_for, tid):
    """A replay must use the configuration in force, or its alerts could never be
    reproduced from the config history."""
    lo, hi = _window(tid)
    r = _post(analytics_client, token_for, tid, lo, hi, mode="replay",
              thresholds={"LAY-02": 1})
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "override_not_allowed"


# -------------------------------------------------------------- guard rails
def test_an_absurd_window_is_refused(analytics_client, token_for, tid):
    lo = datetime(2020, 1, 1, tzinfo=timezone.utc)
    hi = datetime(2026, 1, 1, tzinfo=timezone.utc)
    r = _post(analytics_client, token_for, tid, lo, hi)
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "backfill_refused"
    assert "slices" in r.json()["error"]["message"]


def test_a_backwards_window_is_refused(analytics_client, token_for, tid):
    lo, hi = _window(tid)
    r = _post(analytics_client, token_for, tid, hi, lo)
    assert r.status_code == 400


def test_an_analyst_may_simulate_but_not_replay(analytics_client, token_for, tid):
    lo, hi = _window(tid)
    assert _post(analytics_client, token_for, tid, lo, hi,
                 role="analyst").status_code == 200
    r = _post(analytics_client, token_for, tid, lo, hi, role="analyst", mode="replay")
    assert r.status_code == 403


def test_an_unknown_mode_is_refused(analytics_client, token_for, tid):
    lo, hi = _window(tid)
    r = _post(analytics_client, token_for, tid, lo, hi, mode="obliterate")
    assert r.status_code in (400, 403)


# -------------------------------------------------------------- replay safety
def test_a_replay_does_not_duplicate_alerts_that_already_exist(analytics_client,
                                                               token_for, tid):
    """Duplicating would inflate every dashboard count and break reconciliation."""
    lo, hi = _window(tid)
    first = _post(analytics_client, token_for, tid, lo, hi, mode="replay").json()
    after_first = _counts(tid)["alerts"]

    second = _post(analytics_client, token_for, tid, lo, hi, mode="replay").json()
    assert second["written"] == 0, "a second replay wrote alerts again"
    assert second["skipped_existing"] > 0 or first["written"] == 0
    assert _counts(tid)["alerts"] == after_first

    _cleanup_replay(tid)


def test_a_replay_never_attaches_to_a_case(analytics_client, token_for, tid):
    """An investigator's case must not gain new evidence behind their back - the decision
    may already have been taken, and possibly reported."""
    lo, hi = _window(tid)
    _post(analytics_client, token_for, tid, lo, hi, mode="replay")
    db = SessionLocal()
    try:
        attached = db.scalar(text(
            "SELECT COUNT(*) FROM analytics.fact_alert "
            "WHERE tenant_id = :t AND source = 'replay' AND case_id IS NOT NULL"
        ).bindparams(t=tid))
    finally:
        db.close()
    assert attached == 0
    _cleanup_replay(tid)


def test_replayed_alerts_are_marked_as_such(analytics_client, token_for, tid):
    """So a replayed alert is never mistaken for one the live pipeline raised."""
    lo, hi = _window(tid)
    rep = _post(analytics_client, token_for, tid, lo, hi, mode="replay").json()
    db = SessionLocal()
    try:
        n = db.scalar(text("SELECT COUNT(*) FROM analytics.fact_alert "
                           "WHERE tenant_id = :t AND source = 'replay'")
                      .bindparams(t=tid))
    finally:
        db.close()
    assert n == rep["written"]
    _cleanup_replay(tid)


def test_a_replay_leaves_existing_dispositions_alone(analytics_client, token_for, tid):
    lo, hi = _window(tid)
    db = SessionLocal()
    try:
        before = db.execute(text(
            "SELECT disposition, COUNT(*) FROM analytics.fact_alert "
            "WHERE tenant_id = :t AND source <> 'replay' GROUP BY disposition"),
            {"t": tid}).all()
    finally:
        db.close()
    _post(analytics_client, token_for, tid, lo, hi, mode="replay")
    db = SessionLocal()
    try:
        after = db.execute(text(
            "SELECT disposition, COUNT(*) FROM analytics.fact_alert "
            "WHERE tenant_id = :t AND source <> 'replay' GROUP BY disposition"),
            {"t": tid}).all()
    finally:
        db.close()
    assert sorted(map(tuple, before)) == sorted(map(tuple, after))
    _cleanup_replay(tid)


# -------------------------------------------------------------- point in time
def test_the_context_is_rebuilt_per_day_not_once_per_window():
    """Two failures at once, both silent.

    Lookahead bias makes a naive backfill look better than the live system. And a single
    context built at the end of the window means every transaction is measured against
    the final day's aggregates, so on any window longer than a day the 24-hour rules
    cannot fire at all - the simulator returns confident numbers that are systematically
    wrong for exactly the bands an operator most wants to tune.
    """
    import inspect

    src = inspect.getsource(backfill.run)
    assert "baseline_before=day_start" in src, "the baseline is not cut per day"
    assert "day_end" in src and "timedelta(days=1)" in src, "the window is not sliced"
    assert "as_of = window_to" not in src, "a single window-wide context is back"


def test_a_24_hour_rule_can_fire_in_a_long_simulation(analytics_client, token_for, tid):
    """The regression the day slicing exists to prevent.

    LAY-02 counts distinct counterparties in 24 hours. Scored against one context built
    at the end of a 90-day window it never fired at all, whatever the threshold.
    """
    lo, hi = _window(tid)
    if (hi - lo).days < 2:
        pytest.skip("seeded data spans less than two days")
    body = _post(analytics_client, token_for, tid, lo, hi,
                 thresholds={"LAY-02": 1}).json()
    assert body["would_fire"].get("LAY-02", 0) > 0, (
        "a 24-hour rule fired nothing across a multi-day window - the context is not "
        "being rebuilt per day")


def _cleanup_replay(tid):
    db = SessionLocal()
    try:
        db.execute(text("DELETE FROM analytics.fact_alert "
                        "WHERE tenant_id = :t AND source = 'replay'"), {"t": tid})
        db.commit()
    finally:
        db.close()


# -------------------------------------------------------------- BR-311: try before activating
def test_the_console_can_read_a_rules_operative_threshold():
    """BR-311 drives the simulator from the rule editor, so it has to find the one number
    a band varies. The catalogue marks it ``operative``; guessing which limit to move
    would simulate the wrong change and quietly mislead."""
    import re
    from pathlib import Path

    js = (Path(__file__).resolve().parents[1]
          / "services/gateway/app/static/app.js").read_text(encoding="utf-8")
    assert "function operativeThreshold(" in js
    fn = js[js.index("function operativeThreshold("):]
    fn = fn[:fn.index("\n}\n") + 2]
    assert "b.operative" in fn, "the operative band is not what is read"
    assert "lowerLimit" in fn


def test_the_simulator_the_console_calls_writes_nothing(analytics_client, token_for, tid):
    """The console's 'try against history' posts exactly this. If it ever wrote, an
    operator experimenting with a band would be creating alerts."""
    lo, hi = _window(tid)
    before = _counts(tid)
    r = analytics_client.post(
        f"/analytics/{tid}/detection/backfill", headers=token_for("risk_manager"),
        json={"window_from": lo.isoformat(), "window_to": hi.isoformat(),
              "mode": "simulate", "thresholds": {"LAY-02": 3}})
    assert r.status_code == 200, r.text
    assert r.json()["written"] == 0
    assert _counts(tid) == before
    # And it says so, because the console shows this line to the operator.
    assert "nothing was written" in r.json()["note"].lower()
