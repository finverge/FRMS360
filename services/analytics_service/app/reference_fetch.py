"""Fetching a reference list on a cadence, and deciding whether to activate it (BR-212).

The HTTP call is one small function; everything else here is the decision about whether
what came back should be allowed to replace what is screening today. That decision is the
feature. A scheduler that loads whatever it is given turns a broken feed into silent
non-screening, which is worse than no scheduler at all - a bank would at least know an
unautomated list was going stale.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from .detection.reference import normalise
from .reference_model import LIST_KINDS, ReferenceEntry, ReferenceList
from .reference_source_model import ReferenceSource

#: A list document larger than this is a delivery mistake, not a designation list.
MAX_BYTES = 64 * 1024 * 1024
FETCH_TIMEOUT = 60.0


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
    """Turn a fetched document into ``{key, attributes}`` entries."""
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise FetchRefused(f"document is not UTF-8: {exc}")

    if fmt == "lines":
        return [{"key": ln.strip(), "attributes": {}}
                for ln in text.splitlines()
                # '#' starts a comment in every published list of this shape.
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
                field = key_field or "name"
                key = str(it.get(field) or "").strip()
                if key:
                    out.append({"key": key,
                                "attributes": {k: v for k, v in it.items()
                                               if k != field}})
        return out

    raise FetchRefused(f"unknown format '{fmt}'")


def check_shrinkage(new_count: int, previous_count: int, max_shrink: float) -> None:
    """Refuse a list that lost too much of itself since the last version.

    The failure this prevents: a truncated response, a half-written file, or a login page
    served with a 200, loaded over a live sanctions list. Screening then reports clean
    across the whole tenant and nothing looks wrong.
    """
    if new_count == 0:
        raise FetchRefused(
            "the document contained no entries. An empty screening list would report "
            "clean matches for every counterparty in the tenant.")
    if previous_count <= 0:
        return
    lost = (previous_count - new_count) / previous_count
    if lost > max_shrink:
        raise FetchRefused(
            f"the list dropped from {previous_count:,} to {new_count:,} entries "
            f"({lost:.0%} lost, tolerance {max_shrink:.0%}). That is the shape of a "
            "truncated download, not a republication. The previous version stays active.")


def fetch_bytes(source: ReferenceSource) -> bytes:
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
                    f"document exceeds {MAX_BYTES} bytes; this is not a designation list")
            chunks.append(chunk)
    return b"".join(chunks)


def refresh(db: Session, source: ReferenceSource, *, now: datetime | None = None,
            fetcher=fetch_bytes) -> FetchResult:
    """Fetch, decide, and activate if it should be. Never raises for a feed problem."""
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
            # A daily poll of a weekly list: one request, no new version, no churn in the
            # evidence trail.
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

        # The digest is part of the label, not only the timestamp. Two fetches in
        # the same second collided on the unique constraint, and a version that
        # names the document it holds is more useful anyway: an alert months later
        # can be traced to the exact bytes screened against.
        version = f"{source.kind}-{now:%Y%m%dT%H%M%SZ}-{digest[:8]}"
        row = ReferenceList(
            tenant_id=source.tenant_id, kind=source.kind, version=version,
            source=source.url[:200], entry_count=len(entries), checksum=digest,
            loaded_by="system:reference-fetch", active=False,
            note="Fetched automatically.")
        db.add(row)
        db.flush()
        db.bulk_save_objects([
            ReferenceEntry(tenant_id=source.tenant_id, list_id=row.id, kind=source.kind,
                           match_key=normalise(e["key"]), display_name=e["key"][:300],
                           attributes=e.get("attributes") or {})
            for e in entries])
        # Exactly one active version, flipped in one statement.
        db.query(ReferenceList).filter(
            ReferenceList.tenant_id == source.tenant_id,
            ReferenceList.kind == source.kind,
            ReferenceList.active.is_(True)).update({"active": False})
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
        # A refusal is not an outage. The previous version is still active and still
        # screening; what must not happen is silence.
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


def due_sources(db: Session, now: datetime | None = None) -> list[ReferenceSource]:
    now = now or datetime.now(timezone.utc)
    return list(db.scalars(select(ReferenceSource).where(
        ReferenceSource.enabled.is_(True))).all())


def is_due(source: ReferenceSource, now: datetime) -> bool:
    return source.next_due_at is None or source.next_due_at <= now


def staleness(db: Session, tenant_id: str, now: datetime | None = None) -> list[dict]:
    """How old each active list is, and whether its feed is keeping up.

    Reported per kind including kinds with no source configured, because "nobody set up
    a feed" and "the feed is broken" both end in the same place: screening against an
    old list.
    """
    now = now or datetime.now(timezone.utc)
    sources = {s.kind: s for s in db.scalars(select(ReferenceSource).where(
        ReferenceSource.tenant_id == tenant_id)).all()}
    active = {r.kind: r for r in db.scalars(select(ReferenceList).where(
        ReferenceList.tenant_id == tenant_id,
        ReferenceList.active.is_(True))).all()}

    out = []
    for kind, meta in LIST_KINDS.items():
        src, cur = sources.get(kind), active.get(kind)
        loaded_at = cur.loaded_at if cur else None
        if loaded_at is not None and loaded_at.tzinfo is None:
            loaded_at = loaded_at.replace(tzinfo=timezone.utc)
        age_h = round((now - loaded_at).total_seconds() / 3600, 1) if loaded_at else None
        out.append({
            "kind": kind, "label": meta["label"], "feeds": list(meta["feeds"]),
            "loaded": cur is not None,
            "version": cur.version if cur else "",
            "entry_count": cur.entry_count if cur else 0,
            "age_hours": age_h,
            "automated": src is not None and src.enabled,
            "cadence_hours": src.cadence_hours if src else None,
            "last_success_at": src.last_success_at if src else None,
            "last_error": src.last_error if src else "",
            # The number an operator acts on: a list older than two cadences means the
            # feed has failed twice without anyone noticing.
            "overdue": bool(src and src.enabled and src.next_due_at
                            and src.next_due_at < now),
        })
    return out
