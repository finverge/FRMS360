"""Idempotency keys: make a retried write safe.

The gateway refuses to retry POST, PUT, PATCH and DELETE. That refusal is correct but
expensive: a write whose response is lost to a dropped connection or a load-balancer
timeout leaves the caller unable to tell whether it happened. Retrying might file a second
Fraud Monitoring Return; not retrying might file none. Neither is acceptable, and asking a
compliance officer to go and look is not a design.

A key breaks the tie. The caller labels the *intent* - "this particular RFA flag", not
"another RFA flag" - and the server records the outcome against that label. A retry with
the same key returns the original outcome without executing anything a second time.

Three cases, deliberately distinguished:

* **Same key, same request, already finished** - replay the stored response. This is the
  retry the whole mechanism exists for.
* **Same key, same request, still running** - 409. Two copies of one request are in flight
  (a double-click, or a client retrying before the first attempt returned). Refusing is
  the only safe answer: the first attempt may still commit.
* **Same key, *different* request** - 422. The caller reused a key for different content,
  which is a client bug. Replaying the old response would silently discard the new request
  and returning the new one would make the key meaningless.

Keys are scoped to the authenticated principal. An unscoped key is a cross-tenant leak
waiting to happen: guess someone's key and you receive their response body.
"""
from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import DateTime, Integer, LargeBinary, String, text
from sqlalchemy.orm import Mapped, mapped_column
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse, Response

from .db import Base, SessionLocal
from .schemas_db import PLATFORM

log = logging.getLogger("idempotency")

HEADER = "idempotency-key"
MUTATING = frozenset({"POST", "PUT", "PATCH", "DELETE"})

#: Long enough to cover any retry a human or a client library will attempt, short enough
#: that the table stays small. Well beyond it, "did this happen?" is a question for the
#: audit trail, not a replay cache.
RETENTION = timedelta(hours=24)

#: Responses larger than this are not worth storing. Write endpoints here return small
#: JSON; anything bigger is treated as un-replayable rather than silently truncated.
MAX_STORED_BODY = 256 * 1024


class IdempotencyRecord(Base):
    __tablename__ = "idempotency_keys"
    __table_args__ = {"schema": PLATFORM}

    # The principal is part of the identity, not just a column: one caller's key must
    # never resolve to another caller's stored response.
    scope: Mapped[str] = mapped_column(String(255), primary_key=True)
    key: Mapped[str] = mapped_column(String(200), primary_key=True)
    # Hash of method + path + body, so key reuse with different content is detectable.
    fingerprint: Mapped[str] = mapped_column(String(64))
    state: Mapped[str] = mapped_column(String(12))          # in_progress | completed
    status_code: Mapped[int] = mapped_column(Integer, default=0)
    content_type: Mapped[str] = mapped_column(String(128), default="")
    body: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    body_stored: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


def fingerprint_of(method: str, path: str, body: bytes) -> str:
    h = hashlib.sha256()
    h.update(method.upper().encode())
    h.update(b"\0")
    h.update(path.encode())
    h.update(b"\0")
    h.update(body or b"")
    return h.hexdigest()


def _scope_for(request) -> str:
    """Who is making this request, for key-scoping purposes."""
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        try:
            from .security import decode_token
            claims = decode_token(auth.split(" ", 1)[1])
            return f"{claims.get('tenant_id') or '-'}:{claims.get('sub') or '-'}"
        except Exception:  # noqa: BLE001
            # An unverifiable token is about to be rejected anyway; scope defensively so
            # it cannot reach anyone else's records in the meantime.
            return "unauthenticated:" + hashlib.sha256(auth.encode()).hexdigest()[:32]
    client = request.client.host if request.client else "anonymous"
    return "anonymous:" + client


class IdempotencyMiddleware(BaseHTTPMiddleware):
    """Applies only when the caller supplies a key.

    Opt-in on purpose. A server that invents keys is guessing at intent - it cannot tell
    two deliberate identical writes apart from one write sent twice. Only the caller knows
    that, so only the caller can label it.
    """

    async def dispatch(self, request, call_next):
        if request.method.upper() not in MUTATING:
            return await call_next(request)
        key = request.headers.get(HEADER)
        if not key:
            return await call_next(request)
        if len(key) > 200:
            return JSONResponse(status_code=400, content={"error": {
                "message": "Idempotency-Key must be at most 200 characters.",
                "code": "idempotency_key_too_long"}})

        body = await request.body()
        # Starlette streams the body once; downstream handlers re-read it from here.
        request._body = body

        scope = _scope_for(request)
        fp = fingerprint_of(request.method, request.url.path, body)
        now = datetime.now(timezone.utc)

        claimed, existing = _claim(scope, key, fp, now)

        if not claimed:
            if existing is None:
                # The row vanished between the conflict and the read - expired mid-flight.
                # Treating it as a fresh request is safe: nothing was recorded.
                return await call_next(request)
            if existing["fingerprint"] != fp:
                return JSONResponse(status_code=422, content={"error": {
                    "message": "This Idempotency-Key was already used for a different "
                               "request. Use a new key for a new request.",
                    "code": "idempotency_key_reused"}})
            if existing["state"] == "in_progress":
                return JSONResponse(status_code=409, headers={"Retry-After": "1"},
                                    content={"error": {
                                        "message": "An identical request is still being "
                                                   "processed. Retry shortly.",
                                        "code": "idempotency_in_progress"}})
            if not existing["body_stored"]:
                return JSONResponse(status_code=409, content={"error": {
                    "message": "This request already completed, but its response was too "
                               "large to replay. Re-read the resource to see the result.",
                    "code": "idempotency_response_unavailable"}})
            return Response(
                # psycopg2 hands back bytea as a memoryview, which Starlette tries to
                # .encode() as if it were text.
                content=bytes(existing["body"]) if existing["body"] else b"",
                status_code=existing["status_code"],
                media_type=existing["content_type"] or None,
                headers={"Idempotent-Replay": "true"})

        # We own the key; run the request for real.
        try:
            response = await call_next(request)
        except Exception:
            # Release the claim so the caller's retry is not answered with "in progress"
            # forever after a crash.
            _release(scope, key)
            raise

        chunks = [chunk async for chunk in response.body_iterator]
        payload = b"".join(chunks)

        # A failed write is not an outcome worth pinning: the caller should be able to fix
        # the problem and retry with the same key.
        if response.status_code >= 500:
            _release(scope, key)
        else:
            _complete(scope, key, response.status_code,
                      response.headers.get("content-type", ""), payload, now + RETENTION)

        return Response(content=payload, status_code=response.status_code,
                        headers=dict(response.headers),
                        media_type=response.media_type)


def _claim(scope: str, key: str, fp: str, now: datetime) -> tuple[bool, dict | None]:
    """Atomically take the key, or report what is already there."""
    db = SessionLocal()
    try:
        # ON CONFLICT DO NOTHING makes the claim a single atomic statement, so two
        # replicas racing on the same key cannot both believe they won.
        res = db.execute(text(
            "INSERT INTO platform.idempotency_keys "
            "(scope, key, fingerprint, state, status_code, content_type, body, "
            " body_stored, created_at, expires_at) "
            "VALUES (:scope, :key, :fp, 'in_progress', 0, '', NULL, 1, :now, :exp) "
            "ON CONFLICT (scope, key) DO NOTHING RETURNING key"),
            {"scope": scope, "key": key, "fp": fp, "now": now, "exp": now + RETENTION})
        won = res.first() is not None
        db.commit()
        if won:
            return True, None

        row = db.execute(text(
            "SELECT fingerprint, state, status_code, content_type, body, body_stored, "
            "expires_at FROM platform.idempotency_keys "
            "WHERE scope = :scope AND key = :key"),
            {"scope": scope, "key": key}).mappings().first()
        if row is None:
            return False, None
        if row["expires_at"] and _aware(row["expires_at"]) < now:
            # Expired: drop it and let the caller through as a fresh request.
            db.execute(text("DELETE FROM platform.idempotency_keys "
                            "WHERE scope = :scope AND key = :key"),
                       {"scope": scope, "key": key})
            db.commit()
            return False, None
        return False, dict(row)
    finally:
        db.close()


def _complete(scope: str, key: str, status: int, content_type: str,
              body: bytes, expires: datetime) -> None:
    stored = len(body) <= MAX_STORED_BODY
    db = SessionLocal()
    try:
        db.execute(text(
            "UPDATE platform.idempotency_keys SET state = 'completed', "
            "status_code = :status, content_type = :ct, body = :body, "
            "body_stored = :stored, expires_at = :exp "
            "WHERE scope = :scope AND key = :key"),
            {"status": status, "ct": content_type,
             "body": body if stored else None, "stored": 1 if stored else 0,
             "exp": expires, "scope": scope, "key": key})
        db.commit()
    except Exception as exc:  # noqa: BLE001
        # The write itself already succeeded; failing the response now would be worse
        # than losing replayability for this one key.
        log.warning("could not record idempotent outcome for %s: %s", key, exc)
    finally:
        db.close()


def _release(scope: str, key: str) -> None:
    db = SessionLocal()
    try:
        db.execute(text("DELETE FROM platform.idempotency_keys "
                        "WHERE scope = :scope AND key = :key"),
                   {"scope": scope, "key": key})
        db.commit()
    except Exception as exc:  # noqa: BLE001
        log.warning("could not release idempotency claim %s: %s", key, exc)
    finally:
        db.close()


def _aware(ts: datetime) -> datetime:
    return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)


def purge_expired() -> int:
    """Drop keys past their retention window. Safe to run from a scheduled job."""
    db = SessionLocal()
    try:
        res = db.execute(text("DELETE FROM platform.idempotency_keys "
                              "WHERE expires_at < NOW()"))
        db.commit()
        return res.rowcount or 0
    finally:
        db.close()
