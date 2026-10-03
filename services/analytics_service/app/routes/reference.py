"""Reference-list loading (sanctions, negative list, PEP, CERSAI charges)."""
import hashlib
import json
from datetime import date

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from cp_common import (
    AppError, Principal, get_current_principal, get_session, record_audit,
    resolve_tenant_scope,
)
from cp_common.dynamic_roles import has_permission

from ..detection.reference import availability, normalise
from ..reference_model import LIST_KINDS, ReferenceEntry, ReferenceList

router = APIRouter(prefix="/analytics", tags=["reference-data"])

# Who may do this is data: the tenant's own role rows grant "reference.load"
# (cp_common.permissions); nothing here names a role.
PERMISSION = "reference.load"

#: A guard, not a limit anyone should hit in one request. Real sanctions files arrive by
#: file intake; this endpoint is for a control-plane push and for the console.
MAX_ENTRIES = 100_000


class EntryIn(BaseModel):
    #: The name, or the asset identifier for a charge registry.
    key: str = Field(min_length=1, max_length=300)
    attributes: dict = Field(default_factory=dict)


class LoadIn(BaseModel):
    version: str = Field(min_length=1, max_length=80)
    source: str = Field(default="", max_length=200)
    effective_from: date | None = None
    note: str = Field(default="", max_length=2000)
    entries: list[EntryIn]
    #: False loads the version without switching to it, so it can be inspected first.
    activate: bool = True


def _may(tenant_id: str, principal: Principal) -> bool:
    return has_permission(tenant_id, principal.role, PERMISSION)


@router.get("/{tenant_id}/reference")
def status(
    tenant_id: str,
    db: Session = Depends(get_session),
    principal: Principal = Depends(get_current_principal),
) -> dict:
    """What is loaded, and which indicators are waiting on what."""
    resolve_tenant_scope(principal, tenant_id)
    avail = availability(db, tenant_id)
    rows = db.scalars(select(ReferenceList).where(
        ReferenceList.tenant_id == tenant_id).order_by(
            ReferenceList.loaded_at.desc())).all()
    return {
        "kinds": [{
            "kind": k,
            "label": meta["label"],
            "why": meta["why"],
            "feeds": list(meta["feeds"]),
            "loaded": avail[k].loaded,
            "version": avail[k].version,
            "entry_count": avail[k].entry_count,
            "why_unavailable": avail[k].why_unavailable,
        } for k, meta in LIST_KINDS.items()],
        "versions": [{
            "id": r.id, "kind": r.kind, "version": r.version, "source": r.source,
            "active": r.active, "entry_count": r.entry_count,
            "effective_from": r.effective_from, "checksum": r.checksum,
            "loaded_by": r.loaded_by, "loaded_at": r.loaded_at, "note": r.note,
        } for r in rows],
        "may_load": _may(tenant_id, principal),
    }


@router.post("/{tenant_id}/reference/{kind}")
def load(
    tenant_id: str, kind: str, payload: LoadIn,
    db: Session = Depends(get_session),
    principal: Principal = Depends(get_current_principal),
) -> dict:
    """Load a new version of a list. Never edits an existing one.

    A sanctions list is evidence: when an alert fires months later the bank has to be able
    to say which version of which list matched. So this always creates a version and flips
    a pointer, and the superseded version stays.
    """
    resolve_tenant_scope(principal, tenant_id)
    if kind not in LIST_KINDS:
        raise AppError(f"Unknown reference list '{kind}'. One of: "
                       f"{', '.join(LIST_KINDS)}", 400, "unknown_kind")
    if not _may(tenant_id, principal):
        raise AppError("Your role may not load reference data", 403,
                       "role_not_permitted")
    if not payload.entries:
        # An empty sanctions list that went active would report clean screening for the
        # whole tenant. Refused rather than loaded.
        raise AppError(
            "A reference list must have at least one entry. Loading an empty list would "
            "report clean screening across the whole tenant.", 400, "empty_list")
    if len(payload.entries) > MAX_ENTRIES:
        raise AppError(f"At most {MAX_ENTRIES} entries per load", 413, "too_large")

    if db.scalars(select(ReferenceList).where(
            ReferenceList.tenant_id == tenant_id, ReferenceList.kind == kind,
            ReferenceList.version == payload.version)).first() is not None:
        raise AppError(f"Version '{payload.version}' of this list is already loaded",
                       409, "version_exists")

    digest = hashlib.sha256(json.dumps(
        [[e.key, e.attributes] for e in payload.entries],
        sort_keys=True, default=str).encode()).hexdigest()

    row = ReferenceList(
        tenant_id=tenant_id, kind=kind, version=payload.version,
        source=payload.source.strip(), effective_from=payload.effective_from,
        entry_count=len(payload.entries), checksum=digest,
        loaded_by=principal.subject, note=payload.note.strip(), active=False)
    db.add(row)
    db.flush()

    db.bulk_save_objects([
        ReferenceEntry(tenant_id=tenant_id, list_id=row.id, kind=kind,
                       match_key=normalise(e.key), display_name=e.key[:300],
                       attributes=e.attributes or {})
        for e in payload.entries])

    if payload.activate:
        # Exactly one active version per kind. Done in one statement so no window exists
        # in which a tenant has two active sanctions lists or none.
        db.query(ReferenceList).filter(
            ReferenceList.tenant_id == tenant_id, ReferenceList.kind == kind,
            ReferenceList.active.is_(True)).update({"active": False})
        row.active = True
    db.commit()

    record_audit(
        service="analytics-service", action="reference.loaded", actor=principal.subject,
        actor_role=principal.role, tenant_id=tenant_id, target_type="reference_list",
        target_id=row.id, status="success",
        detail={"kind": kind, "version": payload.version,
                "entries": len(payload.entries), "activated": payload.activate,
                "checksum": digest[:16]})
    return {"id": row.id, "kind": kind, "version": row.version, "active": row.active,
            "entry_count": row.entry_count, "checksum": row.checksum}


@router.post("/{tenant_id}/reference/{kind}/activate/{list_id}")
def activate(
    tenant_id: str, kind: str, list_id: str,
    db: Session = Depends(get_session),
    principal: Principal = Depends(get_current_principal),
) -> dict:
    """Switch which loaded version is screened against - including rolling back."""
    resolve_tenant_scope(principal, tenant_id)
    if not _may(tenant_id, principal):
        raise AppError("Your role may not activate reference data", 403,
                       "role_not_permitted")
    row = db.scalars(select(ReferenceList).where(
        ReferenceList.tenant_id == tenant_id, ReferenceList.kind == kind,
        ReferenceList.id == list_id)).first()
    if row is None:
        raise AppError("Reference list version not found", 404, "not_found")

    db.query(ReferenceList).filter(
        ReferenceList.tenant_id == tenant_id, ReferenceList.kind == kind,
        ReferenceList.active.is_(True)).update({"active": False})
    row.active = True
    db.commit()

    record_audit(
        service="analytics-service", action="reference.activated",
        actor=principal.subject, actor_role=principal.role, tenant_id=tenant_id,
        target_type="reference_list", target_id=row.id, status="success",
        detail={"kind": kind, "version": row.version})
    return {"id": row.id, "kind": kind, "version": row.version, "active": True}
