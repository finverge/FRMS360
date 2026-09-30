"""Lane C batch worker: parse pending statements, compute signals, score, alert.

Run on a timer (cron / Task Scheduler / a k8s CronJob), or continuously with --loop.
Same shape as scripts/run_detection.py - work is claimed with SKIP LOCKED, so several
copies may run at once without duplicating effort.

    python scripts/run_lane_c.py                 # one pass over every tenant
    python scripts/run_lane_c.py --tenant <id>   # one tenant
    python scripts/run_lane_c.py --loop 300      # every 5 minutes
"""
from __future__ import annotations

import argparse
import pathlib
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from sqlalchemy import text  # noqa: E402

from cp_common.db import SessionLocal  # noqa: E402
from services.lane_c_service.app.runner import run_once  # noqa: E402


def tenants(db) -> list[str]:
    """Tenants with a pending statement. Read from Lane C's own schema, not the tenant
    register - the same reasoning run_detection.py's tenants() gives for reading the
    ingestion queue directly instead."""
    return [r[0] for r in db.execute(text(
        "SELECT DISTINCT tenant_id FROM lane_c.financial_statements "
        "WHERE extraction_status = 'pending'")).all()]


def one_pass(tenant: str | None, batch: int) -> int:
    db = SessionLocal()
    total_scored = 0
    try:
        targets = [tenant] if tenant else tenants(db)
        if not targets:
            print("  queue empty")
            return 0
        for tid in targets:
            rep = run_once(db, tid, limit=batch)
            total_scored += rep.scored
            print(f"  {tid[:8]}…  claimed={rep.claimed:<4} parsed={rep.parsed:<4} "
                  f"failed={rep.failed:<3} scored={rep.scored:<4} alerts={rep.alerts}")
            if rep.fired_signals:
                top = sorted(rep.fired_signals.items(), key=lambda kv: -kv[1])[:6]
                print("      fired: " + ", ".join(f"{s}×{n}" for s, n in top))
    finally:
        db.close()
    return total_scored


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tenant", default=None)
    ap.add_argument("--batch", type=int, default=100)
    ap.add_argument("--loop", type=int, default=0,
                    help="Seconds between passes. Omit for a single pass.")
    args = ap.parse_args()

    while True:
        print("lane c pass…")
        one_pass(args.tenant, args.batch)
        if not args.loop:
            return 0
        time.sleep(args.loop)


if __name__ == "__main__":
    raise SystemExit(main())
