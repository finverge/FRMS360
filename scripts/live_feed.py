"""Continuous synthetic feed.

There is no data plane yet, so the Real-Time dashboard would otherwise show a frozen
historical snapshot with an ingestion lag that only grows. This appends a small batch of
traffic on an interval, scored against each tenant's configured rules, so the platform
behaves like something that is actually receiving payments.

It is a demo aid, not a substitute for ingestion: when a real feed exists this is deleted.

    python scripts/live_feed.py                     # every 60s, all tenants
    python scripts/live_feed.py --interval 20 --tenant hdfc-demo
    python scripts/live_feed.py --once              # single batch, for a scheduler
"""
import argparse
import logging
import os
import signal
import sys
import time
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# One line per batch is the useful signal; httpx logs every config fetch.
logging.getLogger("httpx").setLevel(logging.WARNING)

from sqlalchemy import select  # noqa: E402

from cp_common import SessionLocal  # noqa: E402
from scripts.generate_synthetic_data import append_recent  # noqa: E402
from services.tenant_service.app.models import Tenant  # noqa: E402

_stop = False


def _handle_signal(signum, frame):  # noqa: ARG001
    global _stop
    _stop = True
    print("\nstopping after the current batch…")


def run_batch(minutes: int, only: str | None, rate: int) -> int:
    """One batch across the selected tenants. Returns transactions appended."""
    db = SessionLocal()
    total = 0
    try:
        tenants = [t for t in db.scalars(select(Tenant))
                   if (not only or t.slug == only) and t.status == "active"]
        if not tenants:
            print("  no active tenants matched")
            return 0
        for t in tenants:
            # Each tenant is committed separately: one failing feed must not roll back
            # the others.
            try:
                s = append_recent(db, t.id, minutes, rate_per_min=rate)
                total += s["transactions"]
                print(f"  {datetime.now(timezone.utc):%H:%M:%S}  {t.slug:<12} "
                      f"+{s['transactions']:>4} txns  +{s['alerts']:>3} alerts")
            except Exception as exc:  # noqa: BLE001
                db.rollback()
                print(f"  {t.slug:<12} FAILED: {exc}")
    finally:
        db.close()
    return total


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--interval", type=int, default=60, help="seconds between batches")
    ap.add_argument("--minutes", type=int, default=2,
                    help="span of traffic to synthesise per batch")
    ap.add_argument("--rate", type=int, default=45,
                    help="transactions per simulated minute, per tenant")
    ap.add_argument("--tenant", default=None, help="slug; default = every active tenant")
    ap.add_argument("--once", action="store_true", help="one batch then exit")
    args = ap.parse_args()

    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    if args.once:
        run_batch(args.minutes, args.tenant, args.rate)
        return

    print(f"live feed: every {args.interval}s, {args.minutes}m of traffic per batch "
          f"at ~{args.rate}/min ({args.tenant or 'all active tenants'})")
    print("Ctrl+C to stop.\n")
    while not _stop:
        run_batch(args.minutes, args.tenant, args.rate)
        for _ in range(args.interval):
            if _stop:
                break
            time.sleep(1)


if __name__ == "__main__":
    main()
