"""Refresh Lane C's own reference feeds (mca_roc, rating_action) from their configured
sources. Scoped-down mirror of scripts/run_reference_fetch.py's CLI - see that script's
docstring for why a refusal is not an outage and an unchanged document costs one request.

    python scripts/run_lane_c_reference_fetch.py                # what is due
    python scripts/run_lane_c_reference_fetch.py --commit
    python scripts/run_lane_c_reference_fetch.py --commit --force   # ignore the cadence
"""
import argparse
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from cp_common import SessionLocal, record_audit  # noqa: E402
from services.lane_c_service.app import reference_fetch as rf  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--commit", action="store_true", help="actually fetch and load")
    ap.add_argument("--force", action="store_true", help="ignore the cadence")
    ap.add_argument("--tenant", default=None)
    args = ap.parse_args()

    now = datetime.now(timezone.utc)
    db = SessionLocal()
    counts = {"loaded": 0, "unchanged": 0, "refused": 0, "error": 0, "skipped": 0}
    try:
        for src in rf.due_sources(db, now):
            if args.tenant and src.tenant_id != args.tenant:
                continue
            if not args.force and not rf.is_due(src, now):
                counts["skipped"] += 1
                print(f"  {src.tenant_id[:8]} {src.kind:<16} not due until "
                      f"{src.next_due_at:%Y-%m-%d %H:%M}")
                continue
            if not args.commit:
                print(f"  {src.tenant_id[:8]} {src.kind:<16} would fetch {src.url[:60]}")
                continue

            res = rf.refresh(db, src, now=now)
            counts[res.outcome] = counts.get(res.outcome, 0) + 1
            print(f"  {src.tenant_id[:8]} {src.kind:<16} {res.outcome:<10} "
                  f"{res.entries:>7,} entries")
            if res.detail and res.outcome != "unchanged":
                print(f"      {res.detail[:150]}")
            record_audit(
                service="lane-c-service", action="reference.fetched",
                actor="system:lane-c-reference-fetch", actor_role="system",
                tenant_id=src.tenant_id, target_type="reference_source",
                target_id=src.id,
                status="success" if res.outcome in ("loaded", "unchanged") else "failure",
                detail=res.as_dict())
    finally:
        db.close()

    print("\n" + " · ".join(f"{k} {v}" for k, v in counts.items() if v))
    if not args.commit:
        print("Dry run - nothing was fetched. Re-run with --commit.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
