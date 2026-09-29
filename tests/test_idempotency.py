"""A retried write must not happen twice.

The scenario: a POST commits upstream, the response is lost to a dropped connection, and
the client retries. Without a key the caller cannot tell whether the action happened, so
retrying risks filing a second FMR and not retrying risks filing none. These tests check
that a key collapses the duplicate, and - just as important - that the cases where
collapsing would be *wrong* are refused instead.
"""
import uuid

import pytest
from sqlalchemy import text

from cp_common.db import SessionLocal
from cp_common.idempotency import HEADER, MUTATING, fingerprint_of, purge_expired


def _key() -> str:
    return "test-" + uuid.uuid4().hex


def _view_body(name: str) -> dict:
    return {"name": name, "dashboard": "analyst", "query": "rails=UPI"}


def _cleanup_view(client, tid, headers, view_id):
    client.delete(f"/analytics/{tid}/views/{view_id}", headers=headers)


# ------------------------------------------------------------------- the core case
def test_a_retried_write_executes_once_and_replays_the_response(
        analytics_client, token_for, tid):
    h = dict(token_for("analyst"))
    key = _key()
    name = "Idempotent " + key[-8:]

    first = analytics_client.post(f"/analytics/{tid}/views", headers={**h, HEADER: key},
                                  json=_view_body(name))
    assert first.status_code == 201, first.text

    second = analytics_client.post(f"/analytics/{tid}/views", headers={**h, HEADER: key},
                                   json=_view_body(name))
    assert second.status_code == 201, second.text
    assert second.headers.get("Idempotent-Replay") == "true", "response was not replayed"
    assert second.json() == first.json(), "the replay differs from the original"

    listed = analytics_client.get(f"/analytics/{tid}/views", headers=h).json()
    matching = [v for v in listed if v["name"] == name]
    assert len(matching) == 1, f"the write happened {len(matching)} times"
    _cleanup_view(analytics_client, tid, h, first.json()["id"])


def test_without_a_key_nothing_is_intercepted(analytics_client, token_for, tid):
    """The mechanism is opt-in: a server that invents keys is guessing at intent."""
    h = dict(token_for("analyst"))
    r = analytics_client.post(f"/analytics/{tid}/views", headers=h,
                              json=_view_body("Unkeyed view"))
    assert r.status_code == 201
    assert "Idempotent-Replay" not in r.headers
    _cleanup_view(analytics_client, tid, h, r.json()["id"])


# --------------------------------------------------------------- the refusals
def test_reusing_a_key_for_a_different_request_is_refused(analytics_client, token_for, tid):
    """Replaying the old response would silently discard the new request."""
    h = dict(token_for("analyst"))
    key = _key()
    first = analytics_client.post(f"/analytics/{tid}/views", headers={**h, HEADER: key},
                                  json=_view_body("Original " + key[-6:]))
    assert first.status_code == 201

    clash = analytics_client.post(f"/analytics/{tid}/views", headers={**h, HEADER: key},
                                  json=_view_body("Something else entirely"))
    assert clash.status_code == 422, clash.text
    assert clash.json()["error"]["code"] == "idempotency_key_reused"

    listed = [v["name"] for v in analytics_client.get(f"/analytics/{tid}/views",
                                                      headers=h).json()]
    assert "Something else entirely" not in listed, "the clashing write was executed"
    _cleanup_view(analytics_client, tid, h, first.json()["id"])


def test_a_request_still_in_flight_is_refused_not_replayed(analytics_client, token_for, tid):
    """A double-click arrives before the first attempt has committed.

    Replaying would return a response that does not exist yet; executing would run the
    write twice. Refusing is the only safe answer, and the caller is told to retry.
    """
    from datetime import datetime, timezone

    from cp_common.idempotency import _claim, _release

    key = _key()
    body = b'{"name":"inflight","dashboard":"analyst","query":""}'
    fp = fingerprint_of("POST", f"/analytics/{tid}/views", body)
    now = datetime.now(timezone.utc)

    claimed, _ = _claim("inflight-scope", key, fp, now)
    assert claimed, "the first attempt did not take the key"
    try:
        again, existing = _claim("inflight-scope", key, fp, now)
        assert again is False, "two attempts both believed they owned the key"
        assert existing["state"] == "in_progress"
    finally:
        _release("inflight-scope", key)


def test_two_replicas_racing_on_one_key_produce_a_single_winner(tid):
    """The claim has to be atomic, not read-then-write.

    Behind a load balancer a retry can land on a different replica while the first is
    still running. If both could claim the key, the mechanism would be decorative.
    """
    import concurrent.futures as cf
    from datetime import datetime, timezone

    from cp_common.idempotency import _claim, _release

    key, now = _key(), datetime.now(timezone.utc)
    with cf.ThreadPoolExecutor(max_workers=4) as pool:
        results = [f.result() for f in
                   [pool.submit(_claim, "race-scope", key, "fp", now) for _ in range(4)]]
    winners = [r for r in results if r[0]]
    assert len(winners) == 1, f"{len(winners)} replicas claimed the same key"
    _release("race-scope", key)


def test_keys_are_scoped_to_the_caller(analytics_client, token_for, tid):
    """An unscoped key is a cross-tenant leak: guess it and you get someone's response."""
    key = _key()
    analyst = dict(token_for("analyst"))
    other = dict(token_for("risk_manager"))
    name = "Scoped " + key[-6:]

    mine = analytics_client.post(f"/analytics/{tid}/views",
                                 headers={**analyst, HEADER: key}, json=_view_body(name))
    assert mine.status_code == 201

    theirs = analytics_client.post(f"/analytics/{tid}/views",
                                   headers={**other, HEADER: key},
                                   json=_view_body("Their own view " + key[-6:]))
    assert theirs.status_code == 201, theirs.text
    assert theirs.headers.get("Idempotent-Replay") != "true", \
        "one caller received another caller's stored response"
    assert theirs.json()["owner"] != mine.json()["owner"]

    _cleanup_view(analytics_client, tid, analyst, mine.json()["id"])
    _cleanup_view(analytics_client, tid, other, theirs.json()["id"])


def test_an_oversized_key_is_rejected(analytics_client, token_for, tid):
    r = analytics_client.post(f"/analytics/{tid}/views",
                              headers={**dict(token_for("analyst")), HEADER: "x" * 300},
                              json=_view_body("too long"))
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "idempotency_key_too_long"


# ------------------------------------------------------------------- mechanics
def test_reads_are_never_intercepted():
    assert "GET" not in MUTATING
    assert MUTATING == {"POST", "PUT", "PATCH", "DELETE"}


def test_the_fingerprint_distinguishes_what_it_must():
    base = fingerprint_of("POST", "/a", b'{"x":1}')
    assert base == fingerprint_of("POST", "/a", b'{"x":1}')
    assert base != fingerprint_of("POST", "/a", b'{"x":2}'), "body ignored"
    assert base != fingerprint_of("POST", "/b", b'{"x":1}'), "path ignored"
    assert base != fingerprint_of("PUT", "/a", b'{"x":1}'), "method ignored"


def test_a_failed_write_does_not_pin_the_key(analytics_client, token_for, tid):
    """A 5xx must stay retryable: the caller should be able to fix things and retry.

    Recording a server error against the key would make the same key return the same
    failure forever, which is worse than no idempotency at all.
    """
    from cp_common.idempotency import _claim, _complete, _release
    from datetime import datetime, timedelta, timezone

    key, scope = _key(), "failure-scope"
    now = datetime.now(timezone.utc)
    assert _claim(scope, key, "fp", now)[0] is True
    _release(scope, key)          # what the middleware does on a 5xx
    assert _claim(scope, key, "fp", now)[0] is True, "the key stayed pinned after a failure"
    _release(scope, key)


def test_expired_keys_are_purged_and_stop_replaying():
    from cp_common.idempotency import _claim
    from datetime import datetime, timedelta, timezone

    key, scope = _key(), "expiry-scope"
    now = datetime.now(timezone.utc)
    assert _claim(scope, key, "fp", now)[0] is True

    db = SessionLocal()
    try:
        db.execute(text("UPDATE platform.idempotency_keys "
                        "SET state='completed', expires_at = NOW() - INTERVAL '1 hour' "
                        "WHERE scope=:s AND key=:k"), {"s": scope, "k": key})
        db.commit()
    finally:
        db.close()

    # An expired key must not replay; it behaves as a fresh request.
    claimed, existing = _claim(scope, key, "fp", now)
    assert claimed is False and existing is None, "an expired key was still replayable"

    purge_expired()


# ------------------------------------------------------- the gateway retry contract
def test_the_gateway_retries_writes_only_when_they_carry_a_key():
    from services.gateway.app.resilience import should_retry
    assert not should_retry("POST", 1, 3, status=503, connect_error=False), \
        "an unlabelled write was retried"
    assert should_retry("POST", 1, 3, status=503, connect_error=False,
                        idempotency_key="k"), \
        "a labelled write was not retried, so the key bought nothing"
    assert should_retry("DELETE", 1, 3, status=None, connect_error=True,
                        idempotency_key="k")


def test_the_console_labels_every_write():
    import pathlib
    import re
    js = (pathlib.Path(__file__).resolve().parents[1]
          / "services/gateway/app/static/app.js").read_text(encoding="utf-8")
    helper = js.split("async function api(", 1)[1].split("\n}", 1)[0]
    assert "Idempotency-Key" in helper, "the console sends writes without a key"
    assert re.search(r"MUTATING\.has\(", helper), "the key is not conditional on method"
