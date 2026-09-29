"""Give already-declared frauds their staff accountability due date (BR-414).

The clock starts at ``declare_fraud``, so any case declared *before* this feature existed
has a null ``accountability_due_ts`` - and a null due date can never be overdue. The
register would show sixty-five unexamined frauds and zero overdue, which is precisely the
kind of quietly-reassuring number this platform is supposed to make impossible.

The due date is derived, not invented: ``decision_ts + the tenant's own board-approved
window``. Cases with no decision timestamp are skipped and reported rather than given a
guessed date.

    python scripts/backfill_accountability_due.py [--commit]

Runs as a dry run unless --commit is passed.
"""
import argparse
import sys
from datetime import timedelta

from sqlalchemy import text

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[1]))

from cp_common.db import SessionLocal  # noqa: E402
from services.analytics_service.app.rules import policy  # noqa: E402

DECLARED = ("fraud_declared", "fmr_reported", "closed_fraud")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--commit", action="store_true",
                    help="write the changes (otherwise a dry run)")
    args = ap.parse_args()

    db = SessionLocal()
    try:
        tenants = [r[0] for r in db.execute(text(
            "SELECT DISTINCT tenant_id FROM analytics.fact_case "
            "WHERE state = ANY(:s)"), {"s": list(DECLARED)}).all()]

        total_set = total_skipped = 0
        for tid in tenants:
            pol = policy(tid)
            days = int(pol["values"].get("staff_accountability_days", 0) or 0)
            if not days:
                print(f"  {tid}: no staff_accountability_days in policy - skipped")
                continue
            source = "tenant policy" if pol["available"] else "built-in default"

            rows = db.execute(text(
                "SELECT case_id, decision_ts FROM analytics.fact_case "
                "WHERE tenant_id = :t AND state = ANY(:s) "
                "  AND accountability_due_ts IS NULL"),
                {"t": tid, "s": list(DECLARED)}).all()

            missing_decision = [r[0] for r in rows if r[1] is None]
            datable = [r for r in rows if r[1] is not None]
            for case_id, decision_ts in datable:
                if args.commit:
                    db.execute(text(
                        "UPDATE analytics.fact_case SET accountability_due_ts = :d "
                        "WHERE tenant_id = :t AND case_id = :c"),
                        {"d": decision_ts + timedelta(days=days), "t": tid, "c": case_id})
            total_set += len(datable)
            total_skipped += len(missing_decision)
            print(f"  {tid}: {len(datable)} dated at decision + {days}d ({source})"
                  + (f", {len(missing_decision)} skipped with no decision timestamp"
                     if missing_decision else ""))
            if missing_decision:
                # Named, not swallowed: a declared fraud with no decision timestamp is
                # itself a data-quality finding worth chasing.
                print(f"      no decision_ts: {', '.join(missing_decision[:8])}"
                      + (" …" if len(missing_decision) > 8 else ""))

        if args.commit:
            db.commit()
            print(f"\ncommitted: {total_set} case(s) dated, {total_skipped} skipped")
        else:
            print(f"\ndry run: would date {total_set} case(s), skip {total_skipped}."
                  " Re-run with --commit to apply.")
    finally:
        db.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
