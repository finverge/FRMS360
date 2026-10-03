"""Regulatory filing endpoints.

The division of labour is deliberate and worth stating: **the platform prepares, a person
submits, and the platform records what came back.** Nothing here transmits anything to a
regulator. Generating a return does not stop a clock; recording the acknowledgement does.

That is not a limitation to be engineered away. A fraud declaration going to RBI is a
decision a named officer takes, and a system that filed on their behalf would be removing
the accountability the Directions are built around.
"""
import hashlib
import json
from datetime import datetime, timezone

import httpx
from fastapi import APIRouter, Body, Depends, Query
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from cp_common import (
    AppError, Principal, get_current_principal, get_session, record_audit,
    resolve_tenant_scope, settings,
)
from cp_common.dynamic_roles import has_permission

from .. import data_source
from ..filings import builder, narrative_polish, renderers, schema
from ..filings_model import RegulatoryFiling
from ..rules import policy

router = APIRouter(prefix="/analytics", tags=["filings"])

# Who may do this is data: the tenant's own role rows grant "filing.submit"
# (cp_common.permissions); nothing here names a role.
PERMISSION = "filing.submit"


def _may_file(tenant_id: str, principal: Principal) -> bool:
    return has_permission(tenant_id, principal.role, PERMISSION)


def _entity(tenant_id: str) -> dict:
    """Who is filing, from the control plane rather than from a guess."""
    pol = policy(tenant_id)
    out = {"entity_type": pol.get("entity_type"),
           "entity_label": pol.get("entity_label"),
           "governing_direction": pol.get("governing_direction"),
           # BR-504/505: the PMLA Principal Officer named on an STR. A tenant-registry
           # HTTP call has no board authority behind it and would need one built from
           # scratch; the policy config already carries board approval (attestation.py),
           # so the designation rides that instead of a separate mechanism.
           "principal_officer": pol.get("principal_officer_name")}
    try:
        r = httpx.get(f"{settings.tenant_service_url}/tenants/internal/{tenant_id}/identity",
                      headers={"x-internal-key": settings.internal_api_key}, timeout=5.0)
        r.raise_for_status()
        t = r.json()
        out.update({"legal_name": t.get("legal_name"),
                    "display_name": t.get("display_name")})
    except Exception:  # noqa: BLE001
        # Left absent rather than filled with the tenant id. Validation will report the
        # missing entity name, which is the correct outcome - a return naming the wrong
        # institution is worse than one that refuses to generate.
        pass
    return out


def _hash(payload: dict) -> str:
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()


@router.get("/{tenant_id}/cases/{case_id}/filings/{kind}/readiness")
def readiness(
    tenant_id: str, case_id: str, kind: str,
    db: Session = Depends(get_session),
    principal: Principal = Depends(get_current_principal),
) -> dict:
    """Can this case be filed, and if not, what is missing?

    Read-only and available before the deadline, which is the whole point: a bank should
    discover it cannot evidence a fraud while there is still time to go and find the
    evidence, not at the portal on the last day.
    """
    resolve_tenant_scope(principal, tenant_id)
    if kind not in schema.FIELDS:
        raise AppError(f"Unknown return type '{kind}'", 400, "unknown_return")
    try:
        payload = builder.build(db, kind=kind, tenant_id=tenant_id, case_id=case_id,
                                entity=_entity(tenant_id),
                                policy=policy(tenant_id)["values"])
    except LookupError:
        raise AppError("Case not found", 404, "not_found")
    except data_source.NotLiveData as exc:
        # 422, not 403: the caller is permitted to do this, the data is not eligible.
        raise AppError(str(exc), 422, "not_live_data")
    return {**schema.validate(kind, payload).as_dict(),
            "schema_version": schema.SCHEMA_VERSION,
            "binding": schema.FORMAT_BINDINGS.get(kind, {})}


@router.post("/{tenant_id}/cases/{case_id}/filings/{kind}/narrative/polish")
def polish_narrative(
    tenant_id: str, case_id: str, kind: str,
    db: Session = Depends(get_session),
    principal: Principal = Depends(get_current_principal),
) -> dict:
    """Preview an LLM rewrite of the already-built narrative fields, for a
    human to accept, edit, or discard before ``generate``. Read-only —
    nothing here is stored. Gated the same as ``generate``/``download``,
    not as ``readiness``: unlike readiness's gap labels, the narrative
    text itself carries real case evidence, so viewing it is the same
    privileged act as viewing or downloading the pack.

    Off by default (``settings.narrative_polish_enabled``) — a tenant
    that has not turned this on gets a clear 404, not a silent no-op, so
    it is never ambiguous whether the feature ran or was never available.
    """
    resolve_tenant_scope(principal, tenant_id)
    if not settings.narrative_polish_enabled:
        raise AppError("Narrative polishing is not enabled for this deployment",
                       404, "not_enabled")
    if kind not in schema.FIELDS:
        raise AppError(f"Unknown return type '{kind}'", 400, "unknown_return")
    if not _may_file(tenant_id, principal):
        raise AppError("Your role may not view filing narratives", 403,
                       "role_not_permitted")

    try:
        payload = builder.build(db, kind=kind, tenant_id=tenant_id, case_id=case_id,
                                entity=_entity(tenant_id),
                                policy=policy(tenant_id)["values"])
    except LookupError:
        raise AppError("Case not found", 404, "not_found")
    except data_source.NotLiveData as exc:
        raise AppError(str(exc), 422, "not_live_data")

    try:
        results = narrative_polish.polish_narrative_fields(payload)
    except httpx.HTTPError as exc:
        # The rephrasing service being unreachable must not look like "no narrative
        # fields to polish" — that would read as success. It is a real failure of an
        # optional step, reported as one.
        raise AppError(f"Narrative rephrasing service unavailable: {exc}",
                       503, "polish_unavailable")

    record_audit(
        service="analytics-service", action="filing.narrative_polish_previewed",
        actor=principal.subject, actor_role=principal.role, tenant_id=tenant_id,
        target_type="case", target_id=case_id, status="success",
        detail={"kind": kind, "fields": [r.field for r in results],
                "all_verified": all(r.verified for r in results)})

    return {"kind": kind, "results": [
        {"field": r.field, "original": r.original, "polished": r.polished,
         "verified": r.verified, "rejection_reason": r.rejection_reason}
        for r in results
    ]}


class NarrativeOverrides(BaseModel):
    """A human's approved replacement text for one or more narrative
    fields — from the polish preview above, from their own editing, or
    both. Only ever the two known narrative field keys; anything else a
    caller tries to override is rejected outright rather than silently
    ignored, since silently ignoring an unrecognised override could hide
    a client-side bug from whoever is relying on it having taken effect."""
    modus_operandi: str | None = Field(default=None, max_length=8000)
    suspicion_grounds: str | None = Field(default=None, max_length=8000)


@router.post("/{tenant_id}/cases/{case_id}/filings/{kind}/generate")
def generate(
    tenant_id: str, case_id: str, kind: str,
    overrides: NarrativeOverrides | None = Body(default=None),
    db: Session = Depends(get_session),
    principal: Principal = Depends(get_current_principal),
) -> dict:
    """Build and store a return. Refuses if a mandatory field cannot be evidenced.

    ``overrides`` lets a human substitute their own or an LLM-polished narrative for
    the deterministic one builder.build() produced — never anything else in the
    payload. The deterministic text remains the default; an override is always an
    explicit, audited choice, never an automatic substitution.
    """
    resolve_tenant_scope(principal, tenant_id)
    if kind not in schema.FIELDS:
        raise AppError(f"Unknown return type '{kind}'", 400, "unknown_return")
    if not _may_file(tenant_id, principal):
        raise AppError("Your role may not prepare regulatory returns", 403,
                       "role_not_permitted")

    try:
        payload = builder.build(db, kind=kind, tenant_id=tenant_id, case_id=case_id,
                                entity=_entity(tenant_id),
                                policy=policy(tenant_id)["values"])
    except LookupError:
        raise AppError("Case not found", 404, "not_found")
    except data_source.NotLiveData as exc:
        # 422, not 403: the caller is permitted to do this, the data is not eligible.
        raise AppError(str(exc), 422, "not_live_data")

    overridden_fields: list[str] = []
    valid_keys = {f.key for f in schema.FIELDS[kind]}
    if overrides is not None:
        for field_name, value in overrides.model_dump(exclude_none=True).items():
            if field_name not in valid_keys:
                # e.g. suspicion_grounds supplied for an FMR, which has no such field.
                raise AppError(
                    f"'{field_name}' is not a narrative field on a {kind.upper()} return",
                    400, "invalid_override_field")
            if value.strip():
                payload[field_name] = value.strip()
                overridden_fields.append(field_name)

    validation = schema.validate(kind, payload)
    if not validation.ready:
        # Refused rather than generated-with-holes. A return that is missing its amount
        # or its declaration date is not a draft, it is a rejection waiting to happen.
        raise AppError(
            "This case cannot be filed yet: " + ", ".join(
                g.label for g in validation.blocking),
            409, "not_ready_to_file")

    prior = db.scalars(select(RegulatoryFiling).where(
        RegulatoryFiling.tenant_id == tenant_id, RegulatoryFiling.case_id == case_id,
        RegulatoryFiling.kind == kind)).all()
    # An earlier draft that was never submitted is superseded; a submitted one is not
    # touched, because it is a record of what actually went to the regulator.
    for p in prior:
        if p.status == "generated":
            p.status = "superseded"

    row = RegulatoryFiling(
        tenant_id=tenant_id, case_id=case_id, kind=kind,
        schema_version=schema.SCHEMA_VERSION, payload=payload,
        validation=validation.as_dict(), content_hash=_hash(payload),
        revision=len(prior) + 1, generated_by=principal.subject)
    db.add(row)
    db.commit()

    record_audit(
        service="analytics-service", action="filing.generated", actor=principal.subject,
        actor_role=principal.role, tenant_id=tenant_id, target_type="filing",
        target_id=row.id, status="success",
        detail={"case_id": case_id, "kind": kind, "revision": row.revision,
                "hash": row.content_hash[:16], "narrative_overridden": overridden_fields})

    return {"id": row.id, "kind": kind, "revision": row.revision,
            "content_hash": row.content_hash, "status": row.status,
            "validation": row.validation, "generated_at": row.generated_at,
            "binding": schema.FORMAT_BINDINGS.get(kind, {}),
            "narrative_overridden": overridden_fields}


@router.get("/{tenant_id}/cases/{case_id}/filings")
def list_filings(
    tenant_id: str, case_id: str,
    db: Session = Depends(get_session),
    principal: Principal = Depends(get_current_principal),
) -> list[dict]:
    resolve_tenant_scope(principal, tenant_id)
    rows = db.scalars(select(RegulatoryFiling).where(
        RegulatoryFiling.tenant_id == tenant_id,
        RegulatoryFiling.case_id == case_id).order_by(
            RegulatoryFiling.generated_at.desc())).all()
    return [{"id": r.id, "kind": r.kind, "label": schema.RETURN_LABELS.get(r.kind, r.kind),
             "revision": r.revision, "status": r.status,
             "schema_version": r.schema_version, "content_hash": r.content_hash,
             "generated_at": r.generated_at, "generated_by": r.generated_by,
             "submitted_at": r.submitted_at, "submitted_by": r.submitted_by,
             "reference_number": r.reference_number,
             "ready": (r.validation or {}).get("ready")}
            for r in rows]


@router.get("/{tenant_id}/filings/{filing_id}/download")
def download(
    tenant_id: str, filing_id: str,
    fmt: str = Query(default="html"),
    db: Session = Depends(get_session),
    principal: Principal = Depends(get_current_principal),
) -> Response:
    """Render a stored return.

    Rendered from the stored payload, never rebuilt from live data: a return downloaded
    a year later must say what was filed, not what today's record would produce.
    """
    resolve_tenant_scope(principal, tenant_id)
    if not _may_file(tenant_id, principal):
        raise AppError("Your role may not download regulatory returns", 403,
                       "role_not_permitted")
    if fmt not in renderers.RENDERERS:
        raise AppError(f"Unsupported format '{fmt}'", 400, "unknown_format")

    row = db.get(RegulatoryFiling, filing_id)
    if row is None or row.tenant_id != tenant_id:
        raise AppError("Filing not found", 404, "not_found")

    body = renderers.RENDERERS[fmt](row.payload, row.validation or {})
    # The pack carries unmasked customer identifiers, so taking a copy is recorded the
    # same way revealing PII on screen is.
    record_audit(
        service="analytics-service", action="filing.downloaded", actor=principal.subject,
        actor_role=principal.role, tenant_id=tenant_id, target_type="filing",
        target_id=filing_id, status="success",
        detail={"format": fmt, "case_id": row.case_id, "kind": row.kind})

    name = f"{row.kind.upper()}-{row.case_id}-r{row.revision}.{fmt}"
    return Response(content=body, media_type=renderers.MEDIA_TYPES[fmt],
                    headers={"Content-Disposition": f'inline; filename="{name}"'})


class Acknowledge(BaseModel):
    reference_number: str = Field(min_length=1, max_length=120)
    note: str = Field(default="", max_length=2000)


@router.post("/{tenant_id}/filings/{filing_id}/acknowledge")
def acknowledge(
    tenant_id: str, filing_id: str, payload: Acknowledge,
    db: Session = Depends(get_session),
    principal: Principal = Depends(get_current_principal),
) -> dict:
    """Record that this return was actually submitted, and what the regulator said.

    The reference number is the point. "We filed it" is not evidence; the regulator's own
    acknowledgement is, and it is the first thing an inspection asks to see.
    """
    resolve_tenant_scope(principal, tenant_id)
    if not _may_file(tenant_id, principal):
        raise AppError("Your role may not record a submission", 403,
                       "role_not_permitted")

    row = db.get(RegulatoryFiling, filing_id)
    if row is None or row.tenant_id != tenant_id:
        raise AppError("Filing not found", 404, "not_found")
    if row.status == "superseded":
        raise AppError("This version was replaced by a later one; submit that instead",
                       409, "superseded")
    if row.reference_number:
        raise AppError(
            f"Already recorded as submitted under reference {row.reference_number}",
            409, "already_submitted")

    now = datetime.now(timezone.utc)
    row.status = "acknowledged"
    row.submitted_at = now
    row.submitted_by = principal.subject
    row.reference_number = payload.reference_number.strip()
    row.ack_note = payload.note

    # The clock stops here rather than at generation: what discharges the obligation is
    # the submission, not the preparation.
    column = "fmr_filed_ts" if row.kind == "fmr" else "str_filed_ts"
    db.execute(text(
        f"UPDATE analytics.fact_case SET {column} = :now "
        "WHERE tenant_id = :t AND case_id = :c"),
        {"now": now, "t": tenant_id, "c": row.case_id})
    db.commit()

    record_audit(
        service="analytics-service", action="filing.acknowledged",
        actor=principal.subject, actor_role=principal.role, tenant_id=tenant_id,
        target_type="filing", target_id=filing_id, status="success",
        detail={"case_id": row.case_id, "kind": row.kind,
                "reference": row.reference_number})

    return {"id": row.id, "status": row.status,
            "reference_number": row.reference_number, "submitted_at": row.submitted_at,
            "clock_stopped": column}


@router.get("/{tenant_id}/filings/due")
def due(
    tenant_id: str,
    db: Session = Depends(get_session),
    principal: Principal = Depends(get_current_principal),
) -> dict:
    """Everything awaiting a filing, with whether it could be filed today.

    The useful question is not "what is due" - the dashboards answer that - but "what is
    due *and* cannot currently be evidenced", which is the queue somebody has to work
    before the deadline.
    """
    resolve_tenant_scope(principal, tenant_id)
    rows = db.execute(text(
        "SELECT case_id, fmr_due_ts, str_due_ts, fmr_filed_ts, str_filed_ts, "
        "       amount_paise, state FROM analytics.fact_case "
        "WHERE tenant_id = :t AND state NOT IN ('closed_fraud', 'exonerated') "
        "  AND ((fmr_due_ts IS NOT NULL AND fmr_filed_ts IS NULL) "
        "    OR (str_due_ts IS NOT NULL AND str_filed_ts IS NULL)) "
        "ORDER BY LEAST(COALESCE(fmr_due_ts, str_due_ts), "
        "               COALESCE(str_due_ts, fmr_due_ts)) LIMIT 100"),
        {"t": tenant_id}).mappings().all()

    ent, pol = _entity(tenant_id), policy(tenant_id)["values"]
    out = []
    for r in rows:
        for kind, due_col, filed_col in (("fmr", "fmr_due_ts", "fmr_filed_ts"),
                                         ("str", "str_due_ts", "str_filed_ts")):
            if not r[due_col] or r[filed_col]:
                continue
            try:
                payload = builder.build(db, kind=kind, tenant_id=tenant_id,
                                        case_id=r["case_id"], entity=ent, policy=pol)
                v = schema.validate(kind, payload)
                blocking = [g.label for g in v.blocking]
            except LookupError:
                blocking = ["case record unavailable"]
            except data_source.NotLiveData as exc:
                # The queue must survive a case it cannot file. Reporting it as blocked,
                # with the reason, is the whole point of a readiness view — raising here
                # would have made one demonstration case hide every genuinely due return
                # behind a 500.
                blocking = [f"not live data ({', '.join(exc.sources)})"]
            out.append({"case_id": r["case_id"], "kind": kind,
                        "due_at": r[due_col], "state": r["state"],
                        "ready": not blocking, "blocking": blocking})
    return {"count": len(out), "items": out}
