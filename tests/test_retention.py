"""Retention and purge (BR-713).

Two obligations pull opposite ways: DPDP says erase when the purpose is served, RBI says
a fraud file must survive an inspection years later. These tests hold the asymmetry that
resolves it — a tenant may keep data longer than the floor, never for less — and the
rule that a purge which deletes too little is an embarrassment while one that deletes a
fraud file is unrecoverable.
"""
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text

from cp_common.db import SessionLocal
from cp_common.retention import BY_KEY, CLASSES, effective_days, policy_defaults
from cp_common.retention_exec import _PREDICATES, plan, purge


# ------------------------------------------------------------------ the floors
def test_a_tenant_cannot_configure_below_the_floor():
    """The asymmetry the whole design rests on."""
    cls = BY_KEY["cases_fraud"]
    days, raised = effective_days(cls, {cls.policy_key: 30})
    assert days == cls.floor_days, "a 30-day fraud retention was accepted"
    assert raised is True


def test_a_tenant_may_keep_data_longer_than_the_floor():
    cls = BY_KEY["notifications"]
    days, raised = effective_days(cls, {cls.policy_key: 3650})
    assert (days, raised) == (3650, False)


def test_a_missing_or_junk_policy_value_falls_back_to_the_default():
    cls = BY_KEY["transactions"]
    assert effective_days(cls, {})[0] == cls.default_days
    assert effective_days(cls, {cls.policy_key: "soon"})[0] == cls.default_days
    assert effective_days(cls, {cls.policy_key: None})[0] == cls.default_days


def test_the_fraud_floor_outlives_every_other_class():
    """A fraud file must not be purgeable before the evidence that supports it."""
    fraud = BY_KEY["cases_fraud"].floor_days
    for cls in CLASSES:
        if cls.key in ("cases_fraud", "audit"):
            continue
        assert cls.floor_days <= fraud, (
            f"{cls.key} floor ({cls.floor_days}d) exceeds the fraud floor ({fraud}d)")


def test_the_audit_trail_outlives_the_records_it_describes():
    assert BY_KEY["audit"].floor_days >= BY_KEY["cases_non_fraud"].floor_days


def test_every_class_has_a_policy_key_and_a_reason():
    for cls in CLASSES:
        assert cls.policy_key and cls.why, f"{cls.key} is not explained"
    assert set(policy_defaults()) == {c.policy_key for c in CLASSES}


# ------------------------------------------------------- preview equals deletion
def test_the_preview_and_the_deletion_use_the_same_predicate():
    """The preview is what a compliance officer approves.

    A count that does not match what the delete statements touch is worse than no
    preview, because it is relied upon.
    """
    for key, spec in _PREDICATES.items():
        if not spec["delete"]:
            continue
        where = spec["count"].split("WHERE", 1)[1]
        # Every delete for a class must be constrained by the same tenant and cutoff
        # parameters the count used - a delete missing :cut would purge everything.
        for stmt in spec["delete"]:
            assert ":t" in stmt, f"{key}: a delete is not tenant-scoped"
            assert ":cut" in stmt, f"{key}: a delete is not bounded by the cutoff"
        assert ":cut" in where


def test_no_delete_statement_is_unbounded():
    """The failure that ends a company."""
    for key, spec in _PREDICATES.items():
        for stmt in spec["delete"]:
            body = " ".join(stmt.split())
            assert "WHERE" in body.upper(), f"{key}: unbounded DELETE"


def test_declared_frauds_are_never_purged_automatically():
    """Even past the floor. Erasing a fraud file is a deliberate act."""
    assert _PREDICATES["cases_fraud"]["delete"] == [], (
        "the fraud class has delete statements - a nightly job could erase a fraud file")


def test_every_predicate_matches_the_live_schema(tid):
    """A column rename would otherwise surface as a silent zero at 3am.

    Two predicates shipped with the wrong timestamp column - user_sessions has
    issued_at, not created_at, and audit_logs has ts. Both reported "0 eligible" in the
    plan, which is indistinguishable from "nothing to purge" unless you read the
    blocked-reason line. EXPLAIN validates the columns without touching a row.
    """
    db = SessionLocal()
    broken = []
    try:
        for key, spec in _PREDICATES.items():
            for stmt in [spec["count"]] + list(spec["delete"]):
                try:
                    with db.begin_nested():
                        db.execute(text("EXPLAIN " + stmt),
                                   {"t": tid, "cut": datetime(2000, 1, 1,
                                                              tzinfo=timezone.utc)})
                except Exception as exc:  # noqa: BLE001
                    broken.append(f"{key}: {str(exc).splitlines()[0][:120]}")
    finally:
        db.close()
    assert not broken, "retention predicates do not match the schema:\n" + "\n".join(broken)


# ------------------------------------------------------------------ live plan
def test_a_plan_reads_only_and_reports_every_class(tid):
    db = SessionLocal()
    try:
        before = db.execute(text(
            "SELECT COUNT(*) FROM analytics.fact_case WHERE tenant_id = :t"),
            {"t": tid}).scalar()
        plans = plan(db, tid, policy_defaults())
        after = db.execute(text(
            "SELECT COUNT(*) FROM analytics.fact_case WHERE tenant_id = :t"),
            {"t": tid}).scalar()
    finally:
        db.close()
    assert after == before, "planning deleted something"
    assert {p.key for p in plans} == {c.key for c in CLASSES}
    assert all(p.deleted == 0 for p in plans)


def test_a_class_that_cannot_be_evaluated_says_so_rather_than_reporting_zero(tid):
    """"Nothing to purge" and "the query failed" must not look the same."""
    import cp_common.retention_exec as ex

    original = dict(ex._PREDICATES["notifications"])
    ex._PREDICATES["notifications"] = {
        "count": "SELECT COUNT(*) FROM notify.table_that_does_not_exist "
                 "WHERE tenant_id = :t AND created_at < :cut",
        "delete": [],
    }
    try:
        db = SessionLocal()
        try:
            plans = {p.key: p for p in plan(db, tid, policy_defaults())}
        finally:
            db.close()
        p = plans["notifications"]
        assert p.eligible == 0
        assert p.blocked_reason, "a failed count was reported as nothing to purge"
    finally:
        ex._PREDICATES["notifications"] = original


def test_a_recent_fraud_case_is_not_eligible(tid):
    """The case this feature exists to protect."""
    db = SessionLocal()
    now = datetime.now(timezone.utc)
    case_id = f"RET-{int(now.timestamp())}"
    try:
        db.execute(text(
            "INSERT INTO analytics.fact_case (case_id, tenant_id, opened_ts, state, "
            "severity, fmr_category, amount_paise, recovered_paise, rfa_flag, rail, "
            "region, product, customer_segment, assignee, closed_ts) VALUES "
            "(:c, :t, :o, 'closed_fraud', 'critical', 'cheating_and_forgery', 100, 0, "
            "true, 'UPI', 'south', 'savings', 'retail', '', :cl)"),
            {"c": case_id, "t": tid, "o": now - timedelta(days=30), "cl": now})
        db.commit()
        plans = {p.key: p for p in plan(db, tid, policy_defaults())}
        assert plans["cases_fraud"].eligible == 0
        # And it must not be swept up by the non-fraud class either.
        n = db.execute(text(
            "SELECT COUNT(*) FROM analytics.fact_case c WHERE c.tenant_id = :t "
            "AND c.case_id = :c AND c.state NOT IN "
            "('fraud_declared','fmr_reported','closed_fraud')"),
            {"t": tid, "c": case_id}).scalar()
        assert n == 0
    finally:
        db.execute(text("DELETE FROM analytics.fact_case WHERE case_id = :c"),
                   {"c": case_id})
        db.commit()
        db.close()


def test_purge_deletes_only_what_the_plan_offered(tid):
    """Seeds an old notification, confirms the plan sees it and the purge removes it."""
    db = SessionLocal()
    old = datetime.now(timezone.utc) - timedelta(days=3650)
    nid = f"ret-test-{int(datetime.now(timezone.utc).timestamp())}"
    try:
        db.execute(text(
            "INSERT INTO notify.notifications (id, tenant_id, recipient, kind, "
            "dedupe_key, subject, body, severity, context, created_at) VALUES "
            "(:i, :t, 'ret@test.local', 'case_assigned', :d, 'retention probe', '', "
            "'info', '{}'::json, :c)"),
            {"i": nid, "t": tid, "d": nid, "c": old})
        db.commit()

        plans = {p.key: p for p in plan(db, tid, policy_defaults())}
        assert plans["notifications"].eligible >= 1

        done = {p.key: p for p in purge(db, tid, policy_defaults(),
                                        classes=["notifications"])}
        assert done["notifications"].deleted >= 1
        left = db.execute(text("SELECT COUNT(*) FROM notify.notifications WHERE id = :i"),
                          {"i": nid}).scalar()
        assert left == 0
        # Nothing else was touched.
        assert all(p.deleted == 0 for k, p in done.items() if k != "notifications")
    finally:
        db.execute(text("DELETE FROM notify.notifications WHERE id = :i"), {"i": nid})
        db.commit()
        db.close()
