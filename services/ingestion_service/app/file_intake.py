"""Reading a delivered batch file into the inbound queue.

Deliberately knows nothing about directories or SFTP. It takes bytes and a filename and
returns a report, so the same code path is exercised by the tests, by the watcher script
and by an operator uploading through the console. The filesystem parts live in
``scripts/run_file_intake.py``.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .adapters import ADAPTERS, AdapterError, adapt
from . import iso20022
from .file_model import IngestFile, QuarantinedRow
from .models import RawTransaction

#: Refused rather than streamed. A file this size is a delivery mistake - a full-history
#: dump into the nightly folder - and reading it would hold a transaction open for hours.
MAX_FILE_BYTES = 200 * 1024 * 1024
#: Per-row quarantine cap. Past this the file itself is wrong, not the rows.
MAX_QUARANTINE = 5_000


class FileRejected(Exception):
    """The file as a whole cannot be read."""


@dataclass
class FileReport:
    file_id: str
    filename: str
    sha256: str
    state: str
    rows_total: int = 0
    accepted: int = 0
    duplicates: int = 0
    quarantined: int = 0
    error: str = ""
    duplicate_of: str | None = None
    samples: list[dict] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {"file_id": self.file_id, "filename": self.filename,
                "sha256": self.sha256, "state": self.state,
                "rows_total": self.rows_total, "accepted": self.accepted,
                "duplicates": self.duplicates, "quarantined": self.quarantined,
                "error": self.error, "duplicate_of": self.duplicate_of,
                "samples": self.samples}


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def rail_from_name(filename: str) -> str:
    """Infer the rail from the delivered filename, e.g. ``UPI_20260803.csv``.

    Returns "" when it cannot be told, which the caller must treat as a refusal rather
    than a default. Guessing a rail silently would adapt NEFT rows with the UPI mapping
    and land plausible-looking nonsense in the fact table.
    """
    stem = filename.rsplit("/", 1)[-1].split(".")[0].upper()
    for token in stem.replace("-", "_").split("_"):
        if token in ADAPTERS:
            return token
    return ""


def _rows(data: bytes, filename: str) -> tuple[list[dict], list[tuple[int, dict, str]]]:
    """Parse CSV, JSON-lines or ISO 20022 XML into dicts. Returns (rows, unparseable).

    ISO messages are all-or-nothing: their group header states how many transactions the
    file holds and what they total, so a partial parse is a corrupt file rather than a
    file with some bad rows. There is nothing to quarantine - the whole delivery is
    refused and the sender resends it.
    """
    if filename.lower().endswith((".xml", ".iso")) or data.lstrip()[:1] == b"<":
        rows, _header = iso20022.parse(data)
        return [{"_line": i + 1, **r} for i, r in enumerate(rows)], []

    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise FileRejected(f"File is not valid UTF-8: {exc}") from exc
    if not text.strip():
        raise FileRejected("File is empty.")

    bad: list[tuple[int, dict, str]] = []
    if filename.lower().endswith((".jsonl", ".ndjson")):
        rows = []
        for n, line in enumerate(text.splitlines(), start=1):
            if not line.strip():
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError as exc:
                bad.append((n, {"_line": line[:500]}, f"Malformed JSON: {exc}"))
                continue
            if not isinstance(obj, dict):
                bad.append((n, {"_line": line[:500]}, "Line is not a JSON object"))
                continue
            rows.append({"_line": n, **obj})
        return rows, bad

    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        raise FileRejected("File has no header row.")
    # A header of one column almost always means the delimiter is wrong - a
    # semicolon-separated export read as CSV. Better to refuse than to quarantine every
    # row with an unhelpful 'missing field' message.
    if len(reader.fieldnames) == 1 and any(
            d in reader.fieldnames[0] for d in (";", "|", "\t")):
        raise FileRejected(
            f"Header parsed as a single column ({reader.fieldnames[0][:80]!r}); the file "
            "does not appear to be comma-separated.")
    rows = []
    for n, row in enumerate(reader, start=2):     # line 1 is the header
        rows.append({"_line": n, **{k: v for k, v in row.items() if k is not None}})
    return rows, bad


def ingest_bytes(db: Session, *, tenant_id: str, filename: str, data: bytes,
                 rail: str = "", source: str = "file") -> FileReport:
    """Take one delivered file. Idempotent on the file's own digest."""
    if len(data) > MAX_FILE_BYTES:
        raise FileRejected(
            f"File is {len(data)} bytes; the limit is {MAX_FILE_BYTES}. A file this "
            "large in the nightly folder is usually a full-history dump sent by mistake.")

    sha = digest(data)
    prior = db.query(IngestFile).filter(
        IngestFile.tenant_id == tenant_id, IngestFile.sha256 == sha).first()
    if prior is not None:
        # Byte-identical redelivery. Recognised before a single row is read, so a rerun
        # costs nothing and cannot double-count.
        return FileReport(file_id=prior.id, filename=filename, sha256=sha,
                          state="duplicate", rows_total=prior.rows_total,
                          duplicates=prior.rows_accepted, duplicate_of=prior.id,
                          error=f"Already ingested as '{prior.filename}' on "
                                f"{prior.received_at:%Y-%m-%d %H:%M}.")

    rail = (rail or rail_from_name(filename)).upper()
    if not rail and (filename.lower().endswith((".xml", ".iso"))
                     or data.lstrip()[:1] == b"<"):
        # An ISO message does not name a rail - that is a domestic scheme concept. The
        # documented default is used rather than refusing the file, and it is recorded on
        # the ingest record so the choice is visible.
        rail = iso20022.DEFAULT_RAIL
    rec = IngestFile(tenant_id=tenant_id, filename=filename[:400], sha256=sha,
                     size_bytes=len(data), rail=rail, state="processing",
                     started_at=datetime.now(timezone.utc), source=source)
    db.add(rec)
    db.flush()

    if rail not in ADAPTERS:
        rec.state = "failed"
        rec.error = (f"Cannot tell which rail '{filename}' is for. Name the file so it "
                     f"contains one of {', '.join(sorted(ADAPTERS))}, or pass the rail "
                     "explicitly.")
        rec.finished_at = datetime.now(timezone.utc)
        db.commit()
        return FileReport(file_id=rec.id, filename=filename, sha256=sha, state="failed",
                          error=rec.error)

    try:
        rows, unparseable = _rows(data, filename)
    except (FileRejected, AdapterError) as exc:
        # An ISO 20022 file fails as a whole - a control-sum mismatch means the delivery
        # is corrupt, not that some rows are bad - so it lands here rather than in
        # quarantine.
        rec.state = "failed"
        rec.error = str(exc)
        rec.finished_at = datetime.now(timezone.utc)
        db.commit()
        return FileReport(file_id=rec.id, filename=filename, sha256=sha, state="failed",
                          error=str(exc))

    accepted = duplicates = 0
    quarantine: list[tuple[int, dict, str]] = list(unparseable)

    for row in rows:
        line = int(row.pop("_line", 0))
        try:
            canonical = adapt(rail, row)
        except AdapterError as exc:
            if len(quarantine) < MAX_QUARANTINE:
                quarantine.append((line, row, str(exc)))
            continue

        txn = RawTransaction(
            tenant_id=tenant_id, rail=canonical["rail"],
            source_txn_id=canonical["txn_id"], payload=row,
            canonical={**canonical, "ts": canonical["ts"].isoformat(),
                       "beneficiary_added_ts": (
                           canonical["beneficiary_added_ts"].isoformat()
                           if canonical.get("beneficiary_added_ts") else None)},
            ts=canonical["ts"], amount_paise=canonical["amount_paise"],
            state="pending", source=source)
        try:
            # Per-row savepoint, for the same reason the API path uses one: a duplicate
            # is an expected outcome of an overlapping file, not a reason to lose the
            # 49,000 rows after it.
            with db.begin_nested():
                db.add(txn)
                db.flush()
            accepted += 1
        except IntegrityError:
            duplicates += 1

    for line, raw, reason in quarantine:
        db.add(QuarantinedRow(tenant_id=tenant_id, file_id=rec.id, line_number=line,
                              raw={k: str(v)[:500] for k, v in raw.items()},
                              reason=reason[:2000]))

    rec.rows_total = len(rows) + len(unparseable)
    rec.rows_accepted = accepted
    rec.rows_duplicate = duplicates
    rec.rows_quarantined = len(quarantine)
    rec.state = "completed"
    rec.finished_at = datetime.now(timezone.utc)
    db.commit()

    return FileReport(
        file_id=rec.id, filename=filename, sha256=sha, state="completed",
        rows_total=rec.rows_total, accepted=accepted, duplicates=duplicates,
        quarantined=len(quarantine),
        samples=[{"line": ln, "reason": why} for ln, _raw, why in quarantine[:10]])
