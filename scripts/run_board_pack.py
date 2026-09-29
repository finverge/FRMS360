"""Produce board / ACB packs for periods that have finished (BR-507).

Intended for a cron / Task Scheduler tick, typically daily. It is cheap and idempotent:
a period that already has a pack is skipped, so running it every night simply means the
pack for a quarter appears the morning after that quarter closes.

    python scripts/run_board_pack.py                 # dry run, reports what is missing
    python scripts/run_board_pack.py --commit
    python scripts/run_board_pack.py --commit --backfill 4

**What it deliberately does not do is issue.** Generating is mechanical; putting a pack in
front of the committee is a governance act by a named officer, and a script that issued on
their behalf would manufacture the very evidence the record exists to provide. Packs land
as drafts and wait for a person.
"""
import argparse
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import select, text  # noqa: E402

from cp_common import SessionLocal, record_audit  # noqa: E402
from services.analytics_service.app.board_pack import builder  # noqa: E402
from services.analytics_service.app.board_pack.sections import CADENCE_MONTHS  # noqa: E402
from services.analytics_service.app.board_pack_model import BoardPack  # noqa: E402
from services.analytics_service.app.engine.postgres import PostgresEngine  # noqa: E402
from services.analytics_service.app.routes.board_pack import _entity, _hash  # noqa: E402
from services.analytics_service.app.rules import policy  # noqa: E402

ACTOR = "system:board-pack-scheduler"


def _tenants(db) -> list[str]:
    """Every tenant with any case history. A tenant with no cases still owes its board a
    pack, but it has no data to build one from, and an empty pack helps nobody."""
    return [r[0] for r in db.execute(text(
        "SELECT DISTINCT tenant_id FROM analytics.fact_case ORDER BY tenant_id")).all()]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--commit", action="store_true", help="write the packs")
    ap.add_argument("--backfill", type=int, default=1,
                    help="how many completed periods back to cover (default 1)")
    args = ap.parse_args()

    db = SessionLocal()
    made = skipped = 0
    try:
        engine = PostgresEngine(db)
        now = datetime.now(timezone.utc)

        for tid in _tenants(db):
            pol = policy(tid)
            cadence = str(pol["values"].get("board_review_frequency") or "quarterly")
            if cadence not in CADENCE_MONTHS:
                cadence = "quarterly"
            source = "tenant policy" if pol["available"] else "built-in default"

            cursor = builder.period_for(now, cadence)[0]
            for _ in range(max(1, args.backfill)):
                start, end, label = builder.previous_period(cursor, cadence)
                cursor = start

                exists = db.scalars(select(BoardPack).where(
                    BoardPack.tenant_id == tid, BoardPack.cadence == cadence,
                    BoardPack.period_start == start,
                    BoardPack.status.in_(("draft", "issued")))).first()
                if exists is not None:
                    print(f"  {tid} {label}: already has a {exists.status} pack - skipped")
                    skipped += 1
                    continue

                if not args.commit:
                    print(f"  {tid} {label}: would generate ({cadence}, {source})")
                    made += 1
                    continue

                data = builder.build(db, engine, tenant_id=tid, cadence=cadence,
                                     period_start=start, period_end=end,
                                     period_label=label, entity=_entity(tid),
                                     policy=pol["values"])
                row = BoardPack(tenant_id=tid, period_label=label, period_start=start,
                                period_end=end, cadence=cadence, payload=data,
                                content_hash=_hash(data), revision=1,
                                generated_by=ACTOR)
                db.add(row)
                db.commit()
                record_audit(
                    service="analytics-service", action="board_pack.generated",
                    actor=ACTOR, actor_role="system", tenant_id=tid,
                    target_type="board_pack", target_id=row.id, status="success",
                    detail={"period": label, "cadence": cadence, "scheduled": True,
                            "hash": row.content_hash[:16]})
                print(f"  {tid} {label}: generated as a draft "
                      f"({cadence}, {source}, {row.content_hash[:12]}…)")
                made += 1

        verb = "generated" if args.commit else "would generate"
        print(f"\n{verb} {made} pack(s), skipped {skipped} already present.")
        if not args.commit:
            print("Dry run - re-run with --commit to write them.")
        else:
            print("Packs are drafts. Issuing them to the committee is a person's act.")
    finally:
        db.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
