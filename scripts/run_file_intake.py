"""Process batch files dropped on the landing zone.

Layout under the drop root, one folder per tenant::

    drop/<tenant_id>/incoming/UPI_20260803.csv
    drop/<tenant_id>/incoming/UPI_20260803.csv.done     <- written last, by the sender
    drop/<tenant_id>/archive/                            <- handled files land here
    drop/<tenant_id>/failed/                             <- files that could not be read

**Why the .done marker.** SFTP writes are not atomic. A watcher that picks up a file as
soon as it appears will read the first half of a 400MB delivery and report a clean run
over a third of the rows. Waiting for an explicit marker means the sender says when they
have finished, rather than the receiver guessing from file size and occasionally guessing
wrong at 3am. Files with no marker are left alone and reported, not processed.

    python scripts/run_file_intake.py --once
    python scripts/run_file_intake.py --once --drop-root drop
"""
import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from cp_common import SessionLocal, record_audit  # noqa: E402
from services.ingestion_service.app.file_intake import (  # noqa: E402
    FileRejected, ingest_bytes,
)
from services.ingestion_service.app.file_model import DONE_SUFFIX  # noqa: E402

ACTOR = "system:file-intake"


def _move(path: Path, dest_dir: Path) -> Path:
    """Move a handled file aside. Never deletes: the delivery is the evidence."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    target = dest_dir / path.name
    n = 1
    while target.exists():
        target = dest_dir / f"{path.stem}.{n}{path.suffix}"
        n += 1
    path.replace(target)
    return target


def process_tenant(db, tenant_dir: Path, *, commit: bool) -> list[dict]:
    tenant_id = tenant_dir.name
    incoming = tenant_dir / "incoming"
    if not incoming.is_dir():
        return []

    reports = []
    for path in sorted(incoming.iterdir()):
        if path.is_dir() or path.name.endswith(DONE_SUFFIX):
            continue
        marker = path.with_name(path.name + DONE_SUFFIX)
        if not marker.exists():
            # Still uploading, or the sender never confirmed. Left in place.
            reports.append({"file": path.name, "state": "waiting",
                            "detail": f"no {DONE_SUFFIX} marker yet"})
            continue

        if not commit:
            reports.append({"file": path.name, "state": "would_process",
                            "detail": f"{path.stat().st_size} bytes"})
            continue

        data = path.read_bytes()
        try:
            rep = ingest_bytes(db, tenant_id=tenant_id, filename=path.name, data=data)
            out = rep.as_dict()
        except FileRejected as exc:
            out = {"file_id": None, "filename": path.name, "state": "failed",
                   "error": str(exc)}
        except Exception as exc:  # noqa: BLE001
            db.rollback()
            out = {"file_id": None, "filename": path.name, "state": "failed",
                   "error": f"{type(exc).__name__}: {exc}"}

        dest = tenant_dir / ("archive" if out["state"] in ("completed", "duplicate")
                             else "failed")
        moved = _move(path, dest)
        if marker.exists():
            _move(marker, dest)
        out["archived_path"] = str(moved)
        reports.append({"file": path.name, **out})

        record_audit(
            service="ingestion-service", action="ingest.file", actor=ACTOR,
            actor_role="system", tenant_id=tenant_id, target_type="ingest_file",
            target_id=out.get("file_id") or path.name,
            status="success" if out["state"] in ("completed", "duplicate") else "failure",
            detail={k: out.get(k) for k in
                    ("filename", "state", "rows_total", "accepted", "duplicates",
                     "quarantined", "error")})
    return reports


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--drop-root", default=os.environ.get("INGEST_DROP_ROOT", "drop"))
    ap.add_argument("--once", action="store_true", help="single pass (the default)")
    ap.add_argument("--commit", action="store_true",
                    help="actually ingest; otherwise reports what it would take")
    args = ap.parse_args()

    root = Path(args.drop_root)
    if not root.is_dir():
        print(f"drop root '{root}' does not exist - nothing to do.")
        return 0

    db = SessionLocal()
    totals = {"files": 0, "accepted": 0, "duplicates": 0, "quarantined": 0,
              "failed": 0, "waiting": 0}
    try:
        for tenant_dir in sorted(p for p in root.iterdir() if p.is_dir()):
            for rep in process_tenant(db, tenant_dir, commit=args.commit):
                state = rep.get("state")
                if state == "waiting":
                    totals["waiting"] += 1
                    print(f"  {tenant_dir.name}/{rep['file']}: waiting ({rep['detail']})")
                    continue
                if state == "would_process":
                    totals["files"] += 1
                    print(f"  {tenant_dir.name}/{rep['file']}: would process "
                          f"({rep['detail']})")
                    continue
                totals["files"] += 1
                if state == "completed":
                    totals["accepted"] += rep.get("accepted", 0)
                    totals["duplicates"] += rep.get("duplicates", 0)
                    totals["quarantined"] += rep.get("quarantined", 0)
                    print(f"  {tenant_dir.name}/{rep['filename']}: "
                          f"{rep['accepted']} accepted, {rep['duplicates']} duplicate, "
                          f"{rep['quarantined']} quarantined")
                    for s in rep.get("samples", [])[:3]:
                        print(f"        line {s['line']}: {s['reason'][:90]}")
                elif state == "duplicate":
                    totals["duplicates"] += 1
                    print(f"  {tenant_dir.name}/{rep['filename']}: duplicate - "
                          f"{rep.get('error', '')}")
                else:
                    totals["failed"] += 1
                    print(f"  {tenant_dir.name}/{rep['filename']}: FAILED - "
                          f"{rep.get('error', '')[:160]}")
    finally:
        db.close()

    print(f"\nfiles {totals['files']} · accepted {totals['accepted']} · "
          f"duplicate {totals['duplicates']} · quarantined {totals['quarantined']} · "
          f"failed {totals['failed']} · waiting {totals['waiting']}")
    if not args.commit:
        print("Dry run - re-run with --commit to ingest.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
