"""The clock watcher: works out what needs saying, and says it once.

Notifications here are **derived from state, not emitted from events**. A case is either
inside its response window or past it; a return is either filed or overdue. Deriving has
two properties that matter for a compliance system:

* nothing is lost. If this has not run for a day, the next pass finds every condition
  that is true and raises it. An event-based design would have dropped whatever happened
  while the worker was down, and nobody would know which.
* it self-corrects. When a filing is made, the condition stops being true and the
  notification is resolved out of the inbox without anyone dismissing it.

The cost is that the same condition is observed on every pass, which is what the dedupe
key exists to absorb.

    python scripts/run_notifications.py            # one pass, then deliver
    python scripts/run_notifications.py --loop 300 # every five minutes
"""
from __future__ import annotations

import argparse
import pathlib
import sys
import time
from datetime import datetime, timezone

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import httpx  # noqa: E402
from sqlalchemy import text  # noqa: E402

from cp_common import settings  # noqa: E402
from cp_common.db import SessionLocal  # noqa: E402

#: How far ahead of a deadline to warn. Far enough to act, near enough to be relevant.
WARN_DAYS = 3
#: Critical alerts untouched for longer than this are a queue problem, not a backlog.
TRIAGE_HOURS = 4
#: Silence from a bank's feed for longer than this means detection is blind.
FEED_SILENCE_HOURS = 6


def _post(path: str, body: dict) -> dict:
    r = httpx.post(f"{settings.notification_service_url}{path}",
                   headers={"x-internal-key": settings.internal_api_key},
                   json=body, timeout=20.0)
    r.raise_for_status()
    return r.json()


def raise_one(tenant_id: str, recipient: str, kind: str, key: str, ctx: dict,
              link: str = "") -> bool:
    return _post("/internal/notifications/raise",
                 {"tenant_id": tenant_id, "recipient": recipient, "kind": kind,
                  "dedupe_key": key, "context": ctx, "link": link})["created"]


def resolve(tenant_id: str, prefix: str) -> int:
    return _post("/internal/notifications/resolve",
                 {"tenant_id": tenant_id, "dedupe_prefix": prefix})["resolved"]


def recipients_for(db, tenant_id: str, roles: tuple[str, ...]) -> list[str]:
    """Everyone in a tenant holding one of these roles.

    Read from the tenant register rather than a copy, so a leaver stops being notified
    the moment their account goes - a stale distribution list is how a bank ends up
    mailing compliance clocks to someone who left last year.
    """
    rows = db.execute(text(
        "SELECT email FROM tenant.tenant_users "
        "WHERE tenant_id = :t AND role = ANY(:roles)"),
        {"t": tenant_id, "roles": list(roles)}).all()
    return [r[0] for r in rows]


def resolve_owner(db, tenant_id: str, assignee: str,
                  fallback_roles: tuple[str, ...]) -> list[str]:
    """Turn a case's assignee into people who can actually be reached.

    ``fact_case.assignee`` is free text and has historically held short usernames
    ("s.iyer") rather than sign-in addresses. Addressing those directly sent 92% of
    notifications to identifiers with no account behind them - delivered nowhere, read
    by nobody, and invisible because the notification looked successfully raised.

    An unresolvable assignee falls back to the responsible role group, so the case is
    still chased by someone rather than silently orphaned.
    """
    if assignee:
        known = db.execute(text(
            "SELECT email FROM tenant.tenant_users "
            "WHERE tenant_id = :t AND email = :a"),
            {"t": tenant_id, "a": assignee}).scalar()
        if known:
            return [known]
    return recipients_for(db, tenant_id, fallback_roles)


def watch_tenant(db, tenant_id: str, display: str) -> dict:
    now = datetime.now(timezone.utc)
    raised = 0
    resolved = 0

    # ---- natural justice ------------------------------------------------------
    rows = db.execute(text(
        "SELECT case_id, assignee, response_due_ts, "
        "       EXTRACT(EPOCH FROM (response_due_ts - NOW()))/86400 AS days "
        "FROM analytics.fact_case "
        "WHERE tenant_id = :t AND state = 'natural_justice' "
        "  AND response_due_ts IS NOT NULL"), {"t": tenant_id}).mappings().all()
    for r in rows:
        days = float(r["days"] or 0)
        owners = resolve_owner(db, tenant_id, r["assignee"],
                               ("investigator", "risk_manager"))
        if days < 0:
            for who in owners:
                raised += raise_one(tenant_id, who, "nj_window_breached",
                                    f"nj_breach:{r['case_id']}",
                                    {"case_id": r["case_id"], "days": abs(int(days))},
                                    f"/?case={r['case_id']}")
        elif days <= WARN_DAYS:
            for who in owners:
                raised += raise_one(tenant_id, who, "nj_window_closing",
                                    f"nj_closing:{r['case_id']}",
                                    {"case_id": r["case_id"], "days": max(1, int(days))},
                                    f"/?case={r['case_id']}")

    # A case that has moved on is no longer in natural_justice, so its warnings clear.
    open_nj = {r["case_id"] for r in rows}
    for key in ("nj_closing", "nj_breach"):
        stale = db.execute(text(
            "SELECT DISTINCT split_part(dedupe_key, ':', 2) AS case_id "
            "FROM notify.notifications WHERE tenant_id = :t "
            "  AND dedupe_key LIKE :p AND resolved_at IS NULL"),
            {"t": tenant_id, "p": key + ":%"}).all()
        for (case_id,) in stale:
            if case_id not in open_nj:
                resolved += resolve(tenant_id, f"{key}:{case_id}")

    # ---- regulatory filing clocks ---------------------------------------------
    for kind, due_col, filed_col, roles in (
        ("fmr", "fmr_due_ts", "fmr_filed_ts", ("supervisor", "principal_officer",
                                               "risk_manager")),
        ("str", "str_due_ts", "str_filed_ts", ("principal_officer", "supervisor")),
    ):
        rows = db.execute(text(
            f"SELECT case_id, assignee, "
            f"       EXTRACT(EPOCH FROM ({due_col} - NOW()))/86400 AS days "
            f"FROM analytics.fact_case "
            f"WHERE tenant_id = :t AND {due_col} IS NOT NULL AND {filed_col} IS NULL "
            f"  AND state NOT IN ('closed_fraud', 'exonerated')"),
            {"t": tenant_id}).mappings().all()
        owners = recipients_for(db, tenant_id, roles)
        outstanding = set()
        for r in rows:
            days = float(r["days"] or 0)
            overdue = days < 0
            if not overdue and days > WARN_DAYS:
                continue
            key = f"{kind}_{'overdue' if overdue else 'due'}:{r['case_id']}"
            outstanding.add(key)
            for who in owners:
                raised += raise_one(
                    tenant_id, who, f"{kind}_{'overdue' if overdue else 'due'}", key,
                    {"case_id": r["case_id"], "days": max(1, int(abs(days)))},
                    f"/?case={r['case_id']}")

        # Filed, or the case closed: the condition is over.
        live = db.execute(text(
            "SELECT dedupe_key FROM notify.notifications WHERE tenant_id = :t "
            "  AND (dedupe_key LIKE :a OR dedupe_key LIKE :b) AND resolved_at IS NULL"),
            {"t": tenant_id, "a": f"{kind}_due:%", "b": f"{kind}_overdue:%"}).all()
        for (dk,) in live:
            if dk not in outstanding:
                resolved += resolve(tenant_id, dk)

    # ---- dual control waiting on someone --------------------------------------
    rows = db.execute(text(
        "SELECT t.case_id, t.actor FROM cases.case_transitions t "
        "WHERE t.tenant_id = :t AND t.status = 'proposed' "
        "  AND NOT EXISTS (SELECT 1 FROM cases.case_transitions x "
        "                  WHERE x.case_id = t.case_id AND x.action = t.action "
        "                    AND x.status IN ('approved', 'applied') "
        "                    AND x.created_at > t.created_at)"),
        {"t": tenant_id}).mappings().all()
    approvers = recipients_for(db, tenant_id, ("risk_manager", "principal_officer",
                                               "tenant_admin"))
    pending = set()
    for r in rows:
        key = f"approval:{r['case_id']}"
        pending.add(key)
        for who in approvers:
            # The proposer cannot approve their own action, so telling them would be
            # asking for something they are forbidden to do.
            if who == r["actor"]:
                continue
            raised += raise_one(tenant_id, who, "approval_pending", key,
                                {"case_id": r["case_id"], "proposer": r["actor"]},
                                f"/?case={r['case_id']}")
    live = db.execute(text(
        "SELECT DISTINCT dedupe_key FROM notify.notifications WHERE tenant_id = :t "
        "  AND dedupe_key LIKE 'approval:%' AND resolved_at IS NULL"),
        {"t": tenant_id}).all()
    for (dk,) in live:
        if dk not in pending:
            resolved += resolve(tenant_id, dk)

    # ---- assignment ------------------------------------------------------------
    # Derived like everything else. The dedupe key carries the assignee, so reassigning
    # a case tells the new owner without re-telling the old one.
    rows = db.execute(text(
        "SELECT case_id, assignee, severity FROM analytics.fact_case "
        "WHERE tenant_id = :t AND assignee <> '' "
        "  AND state NOT IN ('closed_fraud', 'exonerated')"),
        {"t": tenant_id}).mappings().all()
    for r in rows:
        # Only a real account gets an assignment notice. Falling back to a role group
        # here would tell a whole team a case was assigned to them, which it was not.
        for who in resolve_owner(db, tenant_id, r["assignee"], ()):
            raised += raise_one(tenant_id, who, "case_assigned",
                                f"assigned:{r['case_id']}:{who}",
                                {"case_id": r["case_id"], "severity": r["severity"]},
                                f"/?case={r['case_id']}")

    # ---- triage backlog --------------------------------------------------------
    waiting = db.execute(text(
        "SELECT COUNT(*) FROM analytics.fact_alert "
        "WHERE tenant_id = :t AND severity = 'critical' AND disposition = 'pending' "
        "  AND ts < NOW() - make_interval(hours => :h)"),
        {"t": tenant_id, "h": TRIAGE_HOURS}).scalar() or 0
    if waiting:
        for who in recipients_for(db, tenant_id, ("analyst", "risk_manager")):
            raised += raise_one(tenant_id, who, "critical_alert",
                                "critical_backlog", {"count": waiting,
                                                     "hours": TRIAGE_HOURS}, "/")
    else:
        resolved += resolve(tenant_id, "critical_backlog")

    # ---- is the feed still arriving? -------------------------------------------
    last = db.execute(text(
        "SELECT MAX(received_at) FROM ingestion.raw_transaction WHERE tenant_id = :t"),
        {"t": tenant_id}).scalar()
    if last is not None:
        if last.tzinfo is None:
            last = last.replace(tzinfo=timezone.utc)
        quiet = (now - last).total_seconds() / 3600
        if quiet > FEED_SILENCE_HOURS:
            for who in recipients_for(db, tenant_id, ("tenant_admin", "risk_manager")):
                raised += raise_one(tenant_id, who, "ingestion_stalled",
                                    "feed_silent", {"hours": int(quiet)}, "/")
        else:
            resolved += resolve(tenant_id, "feed_silent")

    return {"tenant": display, "raised": raised, "resolved": resolved}


def one_pass(tenant: str | None) -> None:
    db = SessionLocal()
    try:
        rows = db.execute(text(
            "SELECT id, display_name FROM tenant.tenants "
            "WHERE status = 'active'" + (" AND id = :t" if tenant else "")),
            ({"t": tenant} if tenant else {})).all()
        for tid, name in rows:
            out = watch_tenant(db, tid, name)
            print(f"  {name:<14} raised={out['raised']:<4} resolved={out['resolved']}")
    finally:
        db.close()

    sent = _post("/internal/notifications/deliver", {})
    print(f"  delivery: {sent}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tenant", default=None)
    ap.add_argument("--loop", type=int, default=0, help="seconds between passes")
    args = ap.parse_args()

    while True:
        print("notification pass…")
        one_pass(args.tenant)
        if not args.loop:
            return 0
        time.sleep(args.loop)


if __name__ == "__main__":
    raise SystemExit(main())
