"""Computing what is eligible for purge, and carrying it out.

**This runs as a platform job under the owning database role, never inside a service.**
Retention spans every schema, and no service has - or should have - grants across all of
them: analytics cannot read ``tenant.user_sessions`` and must not be given the ability
to. So this module is imported by ``scripts/run_retention.py``, which connects the way
migrations do. Putting a purge endpoint on a service would mean widening that service's
grants to cover the whole database, which is the opposite of what schema-per-service is
for.


The exclusions in ``retention.CLASSES`` are prose; here they are predicates. Each class
knows how to count what it would delete and how to delete it, and the two use the *same*
WHERE clause - a preview that does not match the deletion is worse than no preview,
because it is the thing an operator approves.

Ordering matters and is not alphabetical: children go before parents, so a purge cannot
orphan an alert from a transaction it cites.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from sqlalchemy import text
from sqlalchemy.orm import Session

from cp_common.retention import CLASSES, effective_days

#: Never delete more than this in one class per run without being asked twice. A purge
#: that suddenly matches a million rows is a policy change or a bug, and either way it
#: should stop and be looked at rather than proceed.
SANITY_LIMIT = 100_000


@dataclass
class ClassPlan:
    key: str
    label: str
    days: int
    floor_applied: bool
    cutoff: datetime
    eligible: int = 0
    deleted: int = 0
    blocked_reason: str = ""
    tables: dict = field(default_factory=dict)


#: (select-count SQL, [delete SQL...]) per class. ``:t`` tenant, ``:cut`` cutoff.
#: Every exclusion from the retention class is expressed here, in SQL.
_PREDICATES: dict[str, dict] = {
    "auth_sessions": {
        "count": "SELECT COUNT(*) FROM tenant.user_sessions "
                 "WHERE tenant_id = :t AND issued_at < :cut",
        "delete": ["DELETE FROM tenant.user_sessions "
                   "WHERE tenant_id = :t AND issued_at < :cut"],
    },
    "notifications": {
        "count": "SELECT COUNT(*) FROM notify.notifications "
                 "WHERE tenant_id = :t AND created_at < :cut",
        "delete": ["DELETE FROM notify.notifications "
                   "WHERE tenant_id = :t AND created_at < :cut"],
    },
    "raw_ingest": {
        # Only what has actually been projected. An unprojected row is work in progress,
        # and deleting it loses a transaction the bank believes it sent us.
        "count": "SELECT COUNT(*) FROM ingestion.raw_transaction "
                 "WHERE tenant_id = :t AND received_at < :cut "
                 "  AND state = 'projected'",
        "delete": ["DELETE FROM ingestion.raw_transaction "
                   "WHERE tenant_id = :t AND received_at < :cut "
                   "  AND state = 'projected'"],
    },
    "alerts_unlinked": {
        "count": "SELECT COUNT(*) FROM analytics.fact_alert "
                 "WHERE tenant_id = :t AND ts < :cut AND case_id IS NULL",
        "delete": ["DELETE FROM analytics.fact_alert "
                   "WHERE tenant_id = :t AND ts < :cut AND case_id IS NULL"],
    },
    "transactions": {
        # Excluded if any alert citing this transaction belongs to a case. That alert is
        # evidence, and evidence that points at a deleted row explains nothing.
        "count": """
            SELECT COUNT(*) FROM analytics.fact_transaction x
             WHERE x.tenant_id = :t AND x.ts < :cut
               AND NOT EXISTS (
                   SELECT 1 FROM analytics.fact_alert a
                    WHERE a.tenant_id = x.tenant_id AND a.txn_id = x.txn_id
                      AND a.case_id IS NOT NULL)
        """,
        "delete": ["""
            DELETE FROM analytics.fact_transaction x
             WHERE x.tenant_id = :t AND x.ts < :cut
               AND NOT EXISTS (
                   SELECT 1 FROM analytics.fact_alert a
                    WHERE a.tenant_id = x.tenant_id AND a.txn_id = x.txn_id
                      AND a.case_id IS NOT NULL)
        """],
    },
    "cases_non_fraud": {
        "count": """
            SELECT COUNT(*) FROM analytics.fact_case c
             WHERE c.tenant_id = :t AND c.closed_ts IS NOT NULL AND c.closed_ts < :cut
               AND c.state NOT IN ('fraud_declared','fmr_reported','closed_fraud')
        """,
        # Children first, so nothing is orphaned if a later statement fails.
        "delete": [
            "DELETE FROM cases.case_transitions WHERE tenant_id = :t AND case_id IN "
            "(SELECT case_id FROM analytics.fact_case WHERE tenant_id = :t "
            " AND closed_ts IS NOT NULL AND closed_ts < :cut "
            " AND state NOT IN ('fraud_declared','fmr_reported','closed_fraud'))",
            "DELETE FROM cases.case_documents WHERE tenant_id = :t AND case_id IN "
            "(SELECT case_id FROM analytics.fact_case WHERE tenant_id = :t "
            " AND closed_ts IS NOT NULL AND closed_ts < :cut "
            " AND state NOT IN ('fraud_declared','fmr_reported','closed_fraud'))",
            "UPDATE analytics.fact_alert SET case_id = NULL WHERE tenant_id = :t "
            "AND case_id IN (SELECT case_id FROM analytics.fact_case WHERE tenant_id = :t "
            " AND closed_ts IS NOT NULL AND closed_ts < :cut "
            " AND state NOT IN ('fraud_declared','fmr_reported','closed_fraud'))",
            "DELETE FROM analytics.fact_case WHERE tenant_id = :t "
            " AND closed_ts IS NOT NULL AND closed_ts < :cut "
            " AND state NOT IN ('fraud_declared','fmr_reported','closed_fraud')",
        ],
    },
    "cases_fraud": {
        # Only closed frauds, only past the long floor, and only when every return that
        # was due has actually been filed. An unfiled return means the obligation is
        # still open, whatever the age of the record.
        "count": """
            SELECT COUNT(*) FROM analytics.fact_case c
             WHERE c.tenant_id = :t AND c.state = 'closed_fraud'
               AND c.closed_ts IS NOT NULL AND c.closed_ts < :cut
               AND (c.fmr_due_ts IS NULL OR c.fmr_filed_ts IS NOT NULL)
               AND (c.str_due_ts IS NULL OR c.str_filed_ts IS NOT NULL)
        """,
        # Deliberately absent: no delete statements. Even past the floor, erasing a
        # declared fraud is not something a nightly job should do unattended. The count
        # is reported so a compliance officer can act on it deliberately.
        "delete": [],
    },
    "audit": {
        "count": "SELECT COUNT(*) FROM platform.audit_logs "
                 "WHERE tenant_id = :t AND ts < :cut",
        "delete": ["DELETE FROM platform.audit_logs "
                   "WHERE tenant_id = :t AND ts < :cut"],
    },
}


def plan(db: Session, tenant_id: str, policy: dict,
         now: datetime | None = None) -> list[ClassPlan]:
    """What each class would delete today. Reads only."""
    now = now or datetime.now(timezone.utc)
    out: list[ClassPlan] = []
    for cls in CLASSES:
        days, raised = effective_days(cls, policy)
        cutoff = now - timedelta(days=days)
        p = ClassPlan(key=cls.key, label=cls.label, days=days, floor_applied=raised,
                      cutoff=cutoff)
        spec = _PREDICATES.get(cls.key)
        if spec is None:
            p.blocked_reason = "No predicate defined for this class."
            out.append(p)
            continue
        try:
            # Per-class savepoint. Without it a single failing count aborts the whole
            # transaction, and every class after it reports "could not evaluate" - one
            # bad predicate would blank the entire plan an operator is reading.
            with db.begin_nested():
                p.eligible = int(db.execute(
                    text(spec["count"]), {"t": tenant_id, "cut": cutoff}).scalar() or 0)
        except Exception as exc:  # noqa: BLE001
            # Reported, never treated as zero: "nothing to purge" and "the query failed"
            # must not look the same to whoever approves the run.
            p.blocked_reason = f"Could not evaluate: {type(exc).__name__}: {exc}"[:200]
        if not spec["delete"]:
            p.blocked_reason = (
                "Eligible, but this class is never purged automatically - erasing a "
                "declared fraud is a deliberate act, not a nightly job.")
        out.append(p)
    return out


def purge(db: Session, tenant_id: str, policy: dict, *, classes: list[str] | None = None,
          now: datetime | None = None, force: bool = False) -> list[ClassPlan]:
    """Carry out the purge. Uses exactly the predicates ``plan`` counted with."""
    plans = plan(db, tenant_id, policy, now)
    for p in plans:
        if classes and p.key not in classes:
            continue
        spec = _PREDICATES.get(p.key)
        if not spec or not spec["delete"] or p.blocked_reason:
            continue
        if p.eligible > SANITY_LIMIT and not force:
            p.blocked_reason = (
                f"{p.eligible:,} rows eligible, over the {SANITY_LIMIT:,} sanity limit. "
                "A jump this size is a policy change or a bug; re-run with --force if it "
                "is intended.")
            continue
        deleted = 0
        for stmt in spec["delete"]:
            deleted += db.execute(text(stmt),
                                  {"t": tenant_id, "cut": p.cutoff}).rowcount or 0
        p.deleted = deleted
    db.commit()
    return plans
