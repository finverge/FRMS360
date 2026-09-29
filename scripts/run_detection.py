"""Detection worker: drain the ingestion queue for every tenant and score what arrives.

Run on a timer (cron / Task Scheduler / a k8s CronJob), or continuously with --loop.
Several copies may run at once: work is claimed with SKIP LOCKED, so each takes a
different slice rather than duplicating effort.

    python scripts/run_detection.py                 # one pass over every tenant
    python scripts/run_detection.py --tenant <id>   # one tenant
    python scripts/run_detection.py --loop 30       # every 30 seconds
"""
from __future__ import annotations

import argparse
import pathlib
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from sqlalchemy import text  # noqa: E402

from cp_common.db import SessionLocal  # noqa: E402
from services.analytics_service.app.detection import run_until_empty  # noqa: E402


def tenants(db) -> list[str]:
    """Tenants with queued work. Read from the ingestion queue rather than the tenant
    register, which analytics has no grant to read - and does not need."""
    return [r[0] for r in db.execute(text(
        "SELECT DISTINCT tenant_id FROM ingestion.raw_transaction "
        "WHERE state IN ('pending', 'claimed')")).all()]


def one_pass(tenant: str | None, batch: int) -> int:
    db = SessionLocal()
    total_alerts = 0
    try:
        targets = [tenant] if tenant else tenants(db)
        if not targets:
            print("  queue empty")
            return 0
        for tid in targets:
            rep = run_until_empty(db, tid, batch=batch)
            total_alerts += rep.alerts
            print(f"  {tid[:8]}…  claimed={rep.claimed:<5} projected={rep.projected:<5} "
                  f"dupes={rep.duplicates:<4} alerts={rep.alerts:<5} cases={rep.cases}")
            if rep.fired_rules:
                top = sorted(rep.fired_rules.items(), key=lambda kv: -kv[1])[:6]
                print("      fired: " + ", ".join(f"{r}×{n}" for r, n in top))
            if rep.failed:
                print(f"      {len(rep.failed)} row(s) failed to project")
    finally:
        db.close()
    return total_alerts


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tenant", default=None)
    ap.add_argument("--batch", type=int, default=500)
    ap.add_argument("--loop", type=int, default=0,
                    help="Seconds between passes. Omit for a single pass.")
    args = ap.parse_args()

    while True:
        print("detection pass…")
        one_pass(args.tenant, args.batch)
        if not args.loop:
            return 0
        time.sleep(args.loop)


if __name__ == "__main__":
    raise SystemExit(main())
