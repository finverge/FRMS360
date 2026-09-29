"""Retention preview and purge (BR-713).

    python scripts/run_retention.py                    # every tenant, dry run
    python scripts/run_retention.py --tenant <id>
    python scripts/run_retention.py --commit
    python scripts/run_retention.py --commit --classes notifications,auth_sessions

Runs under the owning database role, like migrations - retention spans every schema and
no single service has grants across all of them, nor should be given them.

**Dry run is the default and always will be.** The output is what a compliance officer
approves, so the preview and the deletion use the same predicates; a preview that does
not match what runs is worse than no preview at all.
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import text  # noqa: E402

from cp_common import SessionLocal, record_audit  # noqa: E402
from cp_common.retention import CLASSES  # noqa: E402
from cp_common.retention_exec import plan, purge  # noqa: E402

ACTOR = "system:retention"


def _tenants(db, only: str | None) -> list[str]:
    if only:
        return [only]
    return [r[0] for r in db.execute(text(
        "SELECT id FROM tenant.tenants ORDER BY display_name")).all()]


def _policy(tenant_id: str) -> dict:
    """The tenant's own figures. Falls back to defaults if the control plane is down."""
    try:
        from services.analytics_service.app.rules import policy

        return policy(tenant_id)["values"]
    except Exception:  # noqa: BLE001
        return {}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tenant", default=None)
    ap.add_argument("--commit", action="store_true", help="actually delete")
    ap.add_argument("--classes", default="",
                    help="comma-separated class keys; default is all purgeable classes")
    ap.add_argument("--force", action="store_true",
                    help="proceed past the per-class sanity limit")
    args = ap.parse_args()

    only = [c.strip() for c in args.classes.split(",") if c.strip()] or None
    if only:
        unknown = sorted(set(only) - {c.key for c in CLASSES})
        if unknown:
            raise SystemExit(f"Unknown retention class(es): {', '.join(unknown)}")

    db = SessionLocal()
    grand = {"eligible": 0, "deleted": 0}
    try:
        for tid in _tenants(db, args.tenant):
            pol = _policy(tid)
            plans = (purge(db, tid, pol, classes=only, force=args.force)
                     if args.commit else plan(db, tid, pol))
            print(f"\n{tid}")
            for p in plans:
                if only and p.key not in only:
                    continue
                flag = " [floor applied]" if p.floor_applied else ""
                line = (f"  {p.label:<44} {p.days:>5}d{flag:<16} "
                        f"eligible {p.eligible:>7,}")
                if args.commit and p.deleted:
                    line += f"  deleted {p.deleted:>7,}"
                print(line)
                if p.blocked_reason:
                    print(f"      -> {p.blocked_reason}")
                grand["eligible"] += p.eligible
                grand["deleted"] += p.deleted

            if args.commit:
                record_audit(
                    service="platform", action="retention.purge", actor=ACTOR,
                    actor_role="system", tenant_id=tid, target_type="retention",
                    target_id=tid, status="success",
                    detail={p.key: {"days": p.days, "eligible": p.eligible,
                                    "deleted": p.deleted,
                                    "floor_applied": p.floor_applied}
                            for p in plans})
    finally:
        db.close()

    print(f"\ntotal eligible {grand['eligible']:,}"
          + (f" · deleted {grand['deleted']:,}" if args.commit else ""))
    if not args.commit:
        print("Dry run - nothing was deleted. Re-run with --commit to apply.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
