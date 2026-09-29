"""Roll up per-tenant usage for billing (BR-110).

    python scripts/run_usage_rollup.py            # today, every tenant
    python scripts/run_usage_rollup.py --days 30  # backfill, skipping finalised days

Intended for a daily tick shortly after midnight, which finalises yesterday. Safe to run
repeatedly: a day that has already closed is never recomputed, so a rerun cannot change
a figure an invoice was built from.
"""
import argparse
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import text  # noqa: E402

from cp_common import SessionLocal  # noqa: E402
from services.analytics_service.app import usage_service  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=0,
                    help="also roll up this many previous days")
    ap.add_argument("--tenant", default=None)
    args = ap.parse_args()

    now = datetime.now(timezone.utc)
    db = SessionLocal()
    try:
        tenants = ([args.tenant] if args.tenant else
                   [r[0] for r in db.execute(text(
                       "SELECT id FROM tenant.tenants ORDER BY display_name")).all()])
        for tid in tenants:
            if args.days:
                totals = usage_service.backfill(db, tid, days=args.days, now=now)
            else:
                totals = usage_service.compute_day(db, tid, now.date(), now=now)
            summary = " · ".join(f"{k} {v:,}" for k, v in sorted(totals.items()))
            print(f"  {tid[:8]}  {summary}")
    finally:
        db.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
