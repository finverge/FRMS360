"""Ingestion endpoints - the only way real transactions enter the platform."""
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from cp_common import PIPELINE_LAG, QUEUE_DEPTH  # noqa: F401
from cp_common import (
    require_machine_scope,
    AppError, Principal, get_current_principal, get_session, record_audit,
    require_internal_key, resolve_tenant_scope, tenant_status,
)

from ..adapters import RAILS, AdapterError, adapt
from ..cbs_adapter import adapt_cbs
from ..cbs_models import CbsEvent
from ..file_model import IngestFile, QuarantinedRow
from ..models import RawTransaction

router = APIRouter(tags=["ingestion"])

#: One request should not be able to occupy a worker indefinitely. Banks send files;
#: files get split.
MAX_BATCH = 5_000

#: A claim older than this is assumed abandoned (worker crashed) and may be re-taken.
CLAIM_TIMEOUT = timedelta(minutes=15)


class TransactionIn(BaseModel):
    rail: str = Field(min_length=2, max_length=8)
    # Rail payloads differ per rail; the adapter is what gives them a shape.
    payload: dict


class BatchIn(BaseModel):
    transactions: list[TransactionIn] = Field(min_length=1)
    #: Marks the demo corpus so live traffic stays distinguishable from it.
    source: str = Field(default="live", max_length=16)


class AcceptedOut(BaseModel):
    accepted: int
    duplicates: int
    rejected: int
    errors: list[dict]
    batch_id: str


def _refuse_live_from_sandbox(tenant_id: str, source: str) -> None:
    """BR-109: the one enforced half of "isolated sandbox" - a self-service sandbox
    can send whatever synthetic traffic it likes, at any source label except "live".
    Without this a sandbox is isolated in name only: nothing stops its test traffic
    from being counted as the traffic BR-508 (CTR) and BR-611 build regulatory
    filings and live-vs-demo reporting from. Checked here, not left to the caller's
    own honesty, because a sandbox exists precisely so an integrator can experiment
    without needing to get every field right on the first try.
    """
    if source != "live":
        return
    st = tenant_status.standing(tenant_id)
    if st.is_sandbox:
        raise AppError(
            "This is a sandbox tenant; it cannot submit traffic labelled source="
            "'live'. Use any other source value for integration testing, or ask "
            "your Fraud360 contact to promote this tenant to production.",
            422, "sandbox_cannot_be_live")


@router.post("/ingest/{tenant_id}/transactions", response_model=AcceptedOut,
             status_code=202)
def ingest(
    tenant_id: str,
    batch: BatchIn,
    db: Session = Depends(get_session),
    # Accepts a human with the right role, or a machine credential scoped to ingest.
    # A bank's payment switch cannot complete a TOTP challenge, and a human account with
    # MFA switched off would be a worse answer than a credential class that never had an
    # interactive login path - see tenant_service.service_credentials.
    principal: Principal = Depends(require_machine_scope("ingest")),
) -> AcceptedOut:
    """Accept a batch, adapt it, and queue it for detection.

    202 rather than 201: the platform has taken responsibility for these transactions but
    has not yet scored them. Returning 201 would imply detection had run, and a caller
    that believes an alert would already exist is a caller that will not check again.

    A partial batch is accepted partially. Rejecting 5,000 good transactions because one
    row has a malformed timestamp is how banks end up building their own retry queue and
    sending everything twice; each failure is reported with its reason and its index.
    """
    resolve_tenant_scope(principal, tenant_id)
    _refuse_live_from_sandbox(tenant_id, batch.source)
    if len(batch.transactions) > MAX_BATCH:
        raise AppError(f"Batch too large; send at most {MAX_BATCH} transactions.",
                       413, "batch_too_large")

    batch_id = str(uuid.uuid4())
    accepted = duplicates = 0
    errors: list[dict] = []

    for i, item in enumerate(batch.transactions):
        try:
            canonical = adapt(item.rail, item.payload)
        except AdapterError as exc:
            errors.append({"index": i, "rail": item.rail, "error": str(exc)})
            continue

        row = RawTransaction(
            tenant_id=tenant_id, rail=canonical["rail"],
            source_txn_id=canonical["txn_id"], payload=item.payload,
            canonical={**canonical, "ts": canonical["ts"].isoformat(),
                       "beneficiary_added_ts": (
                           canonical["beneficiary_added_ts"].isoformat()
                           if canonical.get("beneficiary_added_ts") else None)},
            ts=canonical["ts"], amount_paise=canonical["amount_paise"],
            state="pending", source=batch.source)
        try:
            # Per-row savepoint: a duplicate is an expected outcome of a replayed file,
            # not a reason to lose the rest of the batch.
            with db.begin_nested():
                db.add(row)
                db.flush()
            accepted += 1
        except IntegrityError:
            # Rolling the savepoint back also detaches the pending instance, so it will
            # not be retried on the next flush and drag the whole batch down with it.
            # (An earlier version added the row before opening the savepoint, which left
            # it pending and made one duplicate poison every later row in the file.)
            duplicates += 1

    db.commit()
    record_audit(
        service="ingestion-service", action="ingest.batch", actor=principal.subject,
        actor_role=principal.role, tenant_id=tenant_id, target_type="batch",
        target_id=batch_id, status="success",
        detail={"accepted": accepted, "duplicates": duplicates,
                "rejected": len(errors), "source": batch.source})

    return AcceptedOut(accepted=accepted, duplicates=duplicates, rejected=len(errors),
                       errors=errors[:50], batch_id=batch_id)


class CbsEventIn(BaseModel):
    payload: dict


class CbsBatchIn(BaseModel):
    events: list[CbsEventIn] = Field(min_length=1)
    source: str = Field(default="cbs", max_length=16)


@router.post("/ingest/{tenant_id}/cbs-events", status_code=202)
def ingest_cbs(
    tenant_id: str,
    batch: CbsBatchIn,
    db: Session = Depends(get_session),
    principal: Principal = Depends(require_machine_scope("ingest")),
) -> dict:
    """Accept non-payment signals from the core banking and loan systems (BR-211).

    Same contract as the payment intake: 202 because the platform has taken
    responsibility but has not scored anything yet, partial acceptance with a reason per
    rejected row, and dedupe on the CBS's own event id so a re-sent nightly extract costs
    nothing.
    """
    resolve_tenant_scope(principal, tenant_id)
    _refuse_live_from_sandbox(tenant_id, batch.source)
    if len(batch.events) > MAX_BATCH:
        raise AppError(f"Batch too large; send at most {MAX_BATCH} events.", 413,
                       "batch_too_large")

    batch_id = str(uuid.uuid4())
    accepted = duplicates = 0
    errors: list[dict] = []

    for i, item in enumerate(batch.events):
        try:
            canonical = adapt_cbs(item.payload)
        except AdapterError as exc:
            errors.append({"index": i, "error": str(exc)})
            continue
        row = CbsEvent(tenant_id=tenant_id, payload=item.payload, state="pending",
                       source=batch.source, **canonical)
        try:
            with db.begin_nested():
                db.add(row)
                db.flush()
            accepted += 1
        except IntegrityError:
            duplicates += 1

    db.commit()
    record_audit(
        service="ingestion-service", action="ingest.cbs_batch", actor=principal.subject,
        actor_role=principal.role, tenant_id=tenant_id, target_type="batch",
        target_id=batch_id, status="success",
        detail={"accepted": accepted, "duplicates": duplicates,
                "rejected": len(errors), "source": batch.source})
    return {"accepted": accepted, "duplicates": duplicates, "rejected": len(errors),
            "errors": errors[:50], "batch_id": batch_id}


@router.get("/ingest/{tenant_id}/cbs-events/coverage")
def cbs_coverage(
    tenant_id: str,
    db: Session = Depends(get_session),
    principal: Principal = Depends(get_current_principal),
) -> dict:
    """Which CBS event kinds have actually arrived, and what each unblocks.

    A feed that was configured once and then went quiet looks identical to one that was
    never configured, unless somebody is told which kinds are missing.
    """
    resolve_tenant_scope(principal, tenant_id)
    from ..cbs_models import EVENT_KINDS

    rows = db.execute(text(
        "SELECT kind, COUNT(*) AS n, MAX(ts) AS latest FROM ingestion.cbs_events "
        "WHERE tenant_id = :t GROUP BY kind"), {"t": tenant_id}).mappings().all()
    have = {r["kind"]: r for r in rows}
    feeds = {
        "loan_disbursement": ["CBS-01", "CBS-02"],
        "loan_utilisation": ["CBS-01"],
        "loan_repayment": ["BEH-02"],
        "cash_withdrawal": ["CBS-02"],
        "sale_proceeds": ["BEH-03"],
        "account_status": [],
        "rm_observation": [],
        "cash_transaction": ["BR-508 (CTR)"],
        "loan_application": ["LOS-01", "LOS-03"],
        "collateral_valuation": ["LOS-02"],
        "cheque_return": ["CBS-04"],
        "od_position": ["CBS-05"],
    }
    return {"kinds": [{
        "kind": k, "what": what, "feeds": feeds.get(k, []),
        "received": int(have[k]["n"]) if k in have else 0,
        "latest": have[k]["latest"] if k in have else None,
    } for k, what in EVENT_KINDS.items()]}


@router.get("/internal/ingest/freshness")
def freshness(db: Session = Depends(get_session)) -> dict:
    """Queue depth and the age of the oldest unprocessed row, across all tenants.

    Refreshes the gauges a scrape reads. Deliberately not per tenant: the metric labels
    would become a customer list, and the on-call question is "is the pipeline moving",
    not "whose pipeline". The per-tenant view is the ``/ingest/{tenant}/status`` endpoint,
    which is authenticated.
    """
    row = db.execute(text(
        "SELECT COUNT(*) AS depth, "
        "       COALESCE(EXTRACT(EPOCH FROM (now() - MIN(received_at))), 0) AS lag "
        "  FROM ingestion.raw_transaction WHERE state = 'pending'")).mappings().first()
    depth, lag = int(row["depth"] or 0), float(row["lag"] or 0.0)
    QUEUE_DEPTH.set(depth, service="ingestion-service", stage="pending")
    PIPELINE_LAG.set(lag, service="ingestion-service", stage="pending")

    stuck = db.execute(text(
        "SELECT COUNT(*) FROM ingestion.raw_transaction "
        " WHERE state = 'claimed' AND claimed_at < now() - interval '15 minutes'")
    ).scalar() or 0
    # A row claimed by a worker that then died is invisible in queue depth - it is not
    # pending - and would sit there silently forever. Surfaced as its own stage.
    QUEUE_DEPTH.set(int(stuck), service="ingestion-service", stage="claimed_stale")

    return {"pending": depth, "oldest_pending_seconds": round(lag, 1),
            "stale_claims": int(stuck)}


@router.get("/ingest/{tenant_id}/files")
def files(
    tenant_id: str,
    limit: int = 50,
    db: Session = Depends(get_session),
    principal: Principal = Depends(get_current_principal),
) -> dict:
    """What has been delivered by file, and how each delivery went."""
    resolve_tenant_scope(principal, tenant_id)
    rows = db.query(IngestFile).filter(
        IngestFile.tenant_id == tenant_id).order_by(
            IngestFile.received_at.desc()).limit(min(limit, 200)).all()
    return {
        "count": len(rows),
        # Surfaced as headline numbers because a file that half-landed is the thing an
        # operator needs to see, and it is invisible in a list of green ticks.
        "quarantined_rows": sum(r.rows_quarantined for r in rows),
        "failed_files": sum(1 for r in rows if r.state == "failed"),
        "files": [{
            "id": r.id, "filename": r.filename, "sha256": r.sha256[:16],
            "rail": r.rail, "state": r.state, "size_bytes": r.size_bytes,
            "rows_total": r.rows_total, "rows_accepted": r.rows_accepted,
            "rows_duplicate": r.rows_duplicate, "rows_quarantined": r.rows_quarantined,
            "error": r.error, "received_at": r.received_at,
            "finished_at": r.finished_at, "archived_path": r.archived_path,
        } for r in rows],
    }


@router.get("/ingest/{tenant_id}/files/{file_id}/quarantine")
def quarantine(
    tenant_id: str, file_id: str,
    limit: int = 200,
    db: Session = Depends(get_session),
    principal: Principal = Depends(get_current_principal),
) -> dict:
    """The rows a delivery could not take, with the line number and the reason.

    The point is that the bank can fix and resend *those rows*, rather than resending the
    whole file and trusting deduplication to sort it out.
    """
    resolve_tenant_scope(principal, tenant_id)
    rows = db.query(QuarantinedRow).filter(
        QuarantinedRow.tenant_id == tenant_id,
        QuarantinedRow.file_id == file_id).order_by(
            QuarantinedRow.line_number).limit(min(limit, 1000)).all()
    return {"file_id": file_id, "count": len(rows),
            "rows": [{"line_number": r.line_number, "reason": r.reason, "raw": r.raw}
                     for r in rows]}


@router.get("/ingest/{tenant_id}/status")
def status(
    tenant_id: str,
    db: Session = Depends(get_session),
    principal: Principal = Depends(get_current_principal),
) -> dict:
    """Queue depth and freshness - what an operator checks before anything else."""
    resolve_tenant_scope(principal, tenant_id)
    rows = db.execute(text(
        "SELECT state, source, COUNT(*) AS n, MAX(received_at) AS latest "
        "FROM ingestion.raw_transaction WHERE tenant_id = :t "
        "GROUP BY state, source"), {"t": tenant_id}).mappings().all()
    by_state: dict[str, int] = {}
    by_source: dict[str, int] = {}
    latest = None
    for r in rows:
        by_state[r["state"]] = by_state.get(r["state"], 0) + r["n"]
        by_source[r["source"]] = by_source.get(r["source"], 0) + r["n"]
        if r["latest"] and (latest is None or r["latest"] > latest):
            latest = r["latest"]
    return {"tenant_id": tenant_id, "by_state": by_state, "by_source": by_source,
            "pending": by_state.get("pending", 0), "last_received_at": latest,
            "supported_rails": list(RAILS)}


# --------------------------------------------------------------- worker-facing API
# Detection claims work over this internal API rather than reaching into the table.
# Ownership stays with ingestion-service, and swapping in a real queue later changes
# these two endpoints and nothing else.

class ClaimIn(BaseModel):
    limit: int = Field(default=500, ge=1, le=MAX_BATCH)


@router.post("/internal/ingest/{tenant_id}/claim",
             dependencies=[Depends(require_internal_key)])
def claim(tenant_id: str, body: ClaimIn, db: Session = Depends(get_session)) -> dict:
    """Hand a worker a batch, exclusively.

    SKIP LOCKED is what makes several detection workers safe: each takes a different
    slice instead of queueing behind one another, and a crashed worker's rows return to
    the queue once its claim ages out rather than being stuck forever.
    """
    token = str(uuid.uuid4())
    now = datetime.now(timezone.utc)
    stale = now - CLAIM_TIMEOUT
    rows = db.execute(text(
        "WITH picked AS ("
        "  SELECT id FROM ingestion.raw_transaction"
        "  WHERE tenant_id = :t"
        "    AND (state = 'pending' OR (state = 'claimed' AND claimed_at < :stale))"
        "  ORDER BY ts"
        "  LIMIT :lim FOR UPDATE SKIP LOCKED)"
        " UPDATE ingestion.raw_transaction r"
        " SET state = 'claimed', claim_token = :tok, claimed_at = :now"
        " FROM picked WHERE r.id = picked.id"
        " RETURNING r.id, r.canonical, r.source"),
        {"t": tenant_id, "lim": body.limit, "tok": token, "now": now, "stale": stale}
    ).mappings().all()
    db.commit()
    return {"claim_token": token, "count": len(rows),
            "rows": [{"id": r["id"], "canonical": r["canonical"], "source": r["source"]}
                     for r in rows]}


class AckIn(BaseModel):
    claim_token: str
    projected: list[str] = Field(default_factory=list)
    failed: list[dict] = Field(default_factory=list)


@router.post("/internal/ingest/{tenant_id}/ack",
             dependencies=[Depends(require_internal_key)])
def ack(tenant_id: str, body: AckIn, db: Session = Depends(get_session)) -> dict:
    """Mark a claimed batch done. Only the holder of the token may close it out."""
    done = 0
    if body.projected:
        done = db.execute(text(
            "UPDATE ingestion.raw_transaction SET state = 'projected', "
            "processed_at = NOW(), claim_token = '' "
            "WHERE tenant_id = :t AND claim_token = :tok AND id = ANY(:ids)"),
            {"t": tenant_id, "tok": body.claim_token,
             "ids": list(body.projected)}).rowcount or 0
    failed = 0
    for f in body.failed:
        failed += db.execute(text(
            "UPDATE ingestion.raw_transaction SET state = 'rejected', "
            "processed_at = NOW(), claim_token = '', error = :err "
            "WHERE tenant_id = :t AND claim_token = :tok AND id = :id"),
            {"t": tenant_id, "tok": body.claim_token, "id": f.get("id"),
             "err": str(f.get("error", ""))[:2000]}).rowcount or 0
    db.commit()
    return {"projected": done, "rejected": failed}
