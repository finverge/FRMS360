"""Fetching a Lane C reference list on a cadence, and deciding whether to activate it.

A scoped-down mirror of analytics_service/app/reference_fetch.py - same mechanism
(fetch, digest-check, parse, shrink-check, version, flip-active), same reasoning: a
scheduler that loads whatever it is given turns a broken feed into silent
non-screening, which is worse than no scheduler at all. Duplicated rather than shared
across services because schema isolation (cp_common.schemas_db) makes a cross-service
import here awkward for no real benefit at two kinds' worth of scale - if a third
consumer of this pattern ever appears, the parsing/shrink-check logic (not the
tenant-scoped models) should move into cp_common as a shared utility instead of being
copied a third time.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from .reference_model import LaneCReferenceEntry, LaneCReferenceList
from .reference_source_model import LaneCReferenceSource

#: A list document larger than this is a delivery mistake, not a designation list.
MAX_BYTES = 64 * 1024 * 1024
FETCH_TIMEOUT = 60.0


def normalise(key: str) -> str:
    """Upper-case, unaccented, punctuation-free - a CIN/PAN compared identically on both
    sides. Same shape as analytics_service/app/detection/reference.py's normalise(),
    kept as a small local copy rather than a cross-service import."""
    if not key:
        return ""
    s = unicodedata.normalize("NFKD", str(key))
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = re.sub(r"[^A-Za-z0-9]+", "", s).upper()
    return s


class FetchRefused(Exception):
    """The document was fetched but must not be activated, with the reason."""


@dataclass
class FetchResult:
    kind: str
    outcome: str            # loaded | unchanged | refused | error
    entries: int = 0
    previous_entries: int = 0
    version: str = ""
    checksum: str = ""
    detail: str = ""

    def as_dict(self) -> dict:
        return {"kind": self.kind, "outcome": self.outcome, "entries": self.entries,
                "previous_entries": self.previous_entries, "version": self.version,
                "checksum": self.checksum[:16], "detail": self.detail}


def parse_document(data: bytes, fmt: str, key_field: str = "") -> list[dict]:
    """Turn a fetched document into ``{key, attributes}`` entries. Identical shape to
    the Lane B parser - see that module's docstring for why each format is handled this
    way."""
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise FetchRefused(f"document is not UTF-8: {exc}")

    if fmt == "lines":
        return [{"key": ln.strip(), "attributes": {}}
                for ln in text.splitlines()
                if ln.strip() and not ln.strip().startswith("#")]

    if fmt == "csv":
        reader = csv.DictReader(io.StringIO(text))
        if not reader.fieldnames:
            raise FetchRefused("CSV has no header row")
        field = key_field or reader.fieldnames[0]
        if field not in reader.fieldnames:
            raise FetchRefused(
                f"key field '{field}' is not in the CSV header "
                f"({', '.join(reader.fieldnames[:8])})")
        return [{"key": (row.get(field) or "").strip(),
                 "attributes": {k: v for k, v in row.items() if k and k != field}}
                for row in reader if (row.get(field) or "").strip()]

    if fmt == "json":
        try:
            doc = json.loads(text)
        except json.JSONDecodeError as exc:
            raise FetchRefused(f"malformed JSON: {exc}")
        items = doc if isinstance(doc, list) else doc.get("entries") or doc.get("data")
        if not isinstance(items, list):
            raise FetchRefused(
                "JSON must be a list, or an object with an 'entries' or 'data' list")
        out = []
        for it in items:
            if isinstance(it, str):
                out.append({"key": it.strip(), "attributes": {}})
            elif isinstance(it, dict):
                field = key_field or "cin"
                key = str(it.get(field) or "").strip()
                if key:
                    out.append({"key": key,
                                "attributes": {k: v for k, v in it.items() if k != field}})
        return out

    raise FetchRefused(f"unknown format '{fmt}'")


def check_shrinkage(new_count: int, previous_count: int, max_shrink: float) -> None:
    """Refuse a list that lost too much of itself since the last version - same guard,
    same reasoning as the Lane B version: a truncated download must not silently
    replace a working list."""
    if new_count == 0:
        raise FetchRefused(
            "the document contained no entries. An empty list would report clean for "
            "every borrower in the tenant.")
    if previous_count <= 0:
        return
    lost = (previous_count - new_count) / previous_count
    if lost > max_shrink:
        raise FetchRefused(
            f"the list dropped from {previous_count:,} to {new_count:,} entries "
            f"({lost:.0%} lost, tolerance {max_shrink:.0%}). That is the shape of a "
            "truncated download, not a republication. The previous version stays active.")


def fetch_bytes(source: LaneCReferenceSource) -> bytes:
    headers = {}
    if source.auth_header and source.auth_value:
        headers[source.auth_header] = source.auth_value
    with httpx.stream("GET", source.url, headers=headers, timeout=FETCH_TIMEOUT,
                      follow_redirects=True) as resp:
        resp.raise_for_status()
        chunks, total = [], 0
        for chunk in resp.iter_bytes():
            total += len(chunk)
            if total > MAX_BYTES:
                raise FetchRefused(
                    f"document exceeds {MAX_BYTES} bytes; this is not a reference list")
            chunks.append(chunk)
    return b"".join(chunks)


def refresh(db: Session, source: LaneCReferenceSource, *, now: datetime | None = None,
            fetcher=fetch_bytes) -> FetchResult:
    """Fetch, decide, and activate if it should be. Never raises for a feed problem -
    identical control flow to the Lane B version."""
    now = now or datetime.now(timezone.utc)
    res = FetchResult(kind=source.kind, outcome="error",
                      previous_entries=source.last_entry_count or 0)
    source.last_attempt_at = now
    source.next_due_at = now + timedelta(hours=max(1, source.cadence_hours or 24))

    try:
        data = fetcher(source)
        digest = hashlib.sha256(data).hexdigest()
        res.checksum = digest

        if digest == source.last_checksum:
            res.outcome = "unchanged"
            res.entries = source.last_entry_count or 0
            res.detail = "The published document has not changed since the last fetch."
            source.last_error = ""
            source.last_success_at = now
            db.commit()
            return res

        entries = parse_document(data, source.fmt, source.key_field)
        res.entries = len(entries)
        check_shrinkage(len(entries), source.last_entry_count or 0, source.max_shrink)

        version = f"{source.kind}-{now:%Y%m%dT%H%M%SZ}-{digest[:8]}"
        row = LaneCReferenceList(
            tenant_id=source.tenant_id, kind=source.kind, version=version,
            source=source.url[:200], entry_count=len(entries), checksum=digest,
            loaded_by="system:lane-c-reference-fetch", active=False,
            note="Fetched automatically.")
        db.add(row)
        db.flush()
        db.bulk_save_objects([
            LaneCReferenceEntry(tenant_id=source.tenant_id, list_id=row.id,
                                kind=source.kind, match_key=normalise(e["key"]),
                                display_name=e["key"][:300],
                                attributes=e.get("attributes") or {})
            for e in entries])
        db.query(LaneCReferenceList).filter(
            LaneCReferenceList.tenant_id == source.tenant_id,
            LaneCReferenceList.kind == source.kind,
            LaneCReferenceList.active.is_(True)).update({"active": False})
        row.active = True

        source.last_checksum = digest
        source.last_entry_count = len(entries)
        source.last_success_at = now
        source.last_error = ""
        res.outcome, res.version = "loaded", version
        res.detail = f"Activated {len(entries):,} entries."
        db.commit()
        return res

    except FetchRefused as exc:
        db.rollback()
        source.last_error = str(exc)[:2000]
        res.outcome, res.detail = "refused", str(exc)
        db.commit()
        return res
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        source.last_error = f"{type(exc).__name__}: {exc}"[:2000]
        res.outcome, res.detail = "error", source.last_error
        db.commit()
        return res


def due_sources(db: Session, now: datetime | None = None) -> list[LaneCReferenceSource]:
    now = now or datetime.now(timezone.utc)
    return list(db.scalars(select(LaneCReferenceSource).where(
        LaneCReferenceSource.enabled.is_(True))).all())


def is_due(source: LaneCReferenceSource, now: datetime) -> bool:
    return source.next_due_at is None or source.next_due_at <= now


def is_loaded(db: Session, tenant_id: str, kind: str) -> bool:
    """Whether ANY active list exists for this kind, independent of a specific key -
    LNC-20 needs this separately from active_entry() because for that signal, presence
    of a matching entry already IS the finding, so "no entry" must be distinguishable
    from "no feed at all" (unmeasurable) rather than folded into one None the way every
    other kind here treats it."""
    return db.scalars(select(LaneCReferenceList).where(
        LaneCReferenceList.tenant_id == tenant_id, LaneCReferenceList.kind == kind,
        LaneCReferenceList.active.is_(True))).first() is not None


def active_entry(db: Session, tenant_id: str, kind: str, key: str) -> dict | None:
    """The active list's entry for one borrower key, or None if no list is loaded for
    this kind, or this borrower isn't in it. The one lookup both signal functions need -
    kept here, not duplicated in each, since "is a list even loaded" and "does this key
    match" are the same two questions every consumer of this mechanism asks."""
    if not key:
        return None
    active_list = db.scalars(select(LaneCReferenceList).where(
        LaneCReferenceList.tenant_id == tenant_id, LaneCReferenceList.kind == kind,
        LaneCReferenceList.active.is_(True))).first()
    if active_list is None:
        return None
    entry = db.scalars(select(LaneCReferenceEntry).where(
        LaneCReferenceEntry.list_id == active_list.id,
        LaneCReferenceEntry.match_key == normalise(key))).first()
    if entry is None:
        return None
    return {"attributes": entry.attributes, "version": active_list.version}
