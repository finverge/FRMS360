"""Edge behaviour when an upstream is failing.

The failure being designed against: one slow upstream used to hold connections for the
full timeout, fill the pool, and make requests to healthy services queue behind it - so a
degraded analytics-service took the whole console down. These tests check the mechanisms
that stop that, and the deliberate refusals (writes are never retried) that go with them.
"""
import pytest

from services.gateway.app.resilience import (
    RETRYABLE_METHODS, CircuitBreaker, CircuitOpen, RateLimiter, backoff_seconds,
    should_retry,
)


# ------------------------------------------------------------------ circuit breaker
def test_the_breaker_opens_after_repeated_failures():
    cb = CircuitBreaker("analytics", failure_threshold=3, reset_after_seconds=60)
    assert cb.state == "closed"
    for _ in range(3):
        cb.before_request()
        cb.record_failure()
    assert cb.state == "open"
    with pytest.raises(CircuitOpen):
        cb.before_request()


def test_an_open_breaker_fails_immediately_rather_than_waiting():
    """The whole point: a known-bad upstream must not occupy a connection for 25s."""
    cb = CircuitBreaker("analytics", failure_threshold=1, reset_after_seconds=60)
    cb.record_failure()
    with pytest.raises(CircuitOpen) as err:
        cb.before_request()
    assert err.value.retry_after > 0, "no Retry-After hint for the caller"


def test_success_closes_the_breaker_again():
    cb = CircuitBreaker("analytics", failure_threshold=2, reset_after_seconds=60)
    cb.record_failure()
    cb.record_success()
    cb.record_failure()
    assert cb.state == "closed", "failures did not reset after a success"


def test_a_recovered_upstream_is_probed_once_not_stormed():
    """Half-open must admit exactly one probe.

    Letting every waiting request through the instant the cool-down expires re-breaks the
    service at the moment it starts recovering.
    """
    cb = CircuitBreaker("analytics", failure_threshold=1, reset_after_seconds=0)
    cb.record_failure()
    assert cb.state == "half_open"
    cb.before_request()                 # the one probe
    with pytest.raises(CircuitOpen):    # everyone else still waits
        cb.before_request()


def test_a_failed_probe_reopens_the_breaker():
    cb = CircuitBreaker("analytics", failure_threshold=1, reset_after_seconds=30)
    cb.record_failure()
    cb._opened_at -= 31                 # cool-down elapsed
    assert cb.state == "half_open"
    cb.before_request()
    cb.record_failure()
    assert cb.state == "open", "a failed probe should restart the cool-down"


def test_breakers_are_per_upstream():
    """A failing analytics-service must not deny service to auth."""
    from services.gateway.app.main import BREAKERS
    assert len(BREAKERS) > 1
    assert len({id(b) for b in BREAKERS.values()}) == len(BREAKERS), \
        "upstreams share a breaker; one failure would take out the others"


# --------------------------------------------------------------------------- retry
@pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE"])
def test_writes_are_never_retried(method):
    """A retried write can become two.

    Until idempotency keys exist, a POST whose response was lost may already have
    succeeded upstream. Refusing to retry is the safe answer, and it is deliberate.
    """
    assert not should_retry(method, attempt=1, max_attempts=3, status=503,
                            connect_error=False)
    assert not should_retry(method, attempt=1, max_attempts=3, status=None,
                            connect_error=True)
    assert method not in RETRYABLE_METHODS


@pytest.mark.parametrize("status", [502, 503, 504])
def test_reads_are_retried_when_the_upstream_says_it_cannot_serve(status):
    assert should_retry("GET", attempt=1, max_attempts=3, status=status,
                        connect_error=False)


def test_a_genuine_application_error_is_not_retried():
    """500 will fail identically on retry; the delay helps nobody."""
    assert not should_retry("GET", 1, 3, status=500, connect_error=False)
    assert not should_retry("GET", 1, 3, status=404, connect_error=False)
    assert not should_retry("GET", 1, 3, status=200, connect_error=False)


def test_retries_are_bounded():
    assert not should_retry("GET", attempt=3, max_attempts=3, status=503,
                            connect_error=False)


def test_backoff_grows_but_stays_bounded():
    delays = [backoff_seconds(i) for i in range(1, 6)]
    assert delays == sorted(delays), "backoff must not shrink"
    assert max(delays) <= 0.5, "a human is waiting; backoff must stay short"


# ---------------------------------------------------------------------- rate limit
def test_a_runaway_caller_is_throttled():
    rl = RateLimiter(rate_per_second=1000, burst=5)
    allowed = [rl.check("noisy")[0] for _ in range(8)]
    assert allowed[:5] == [True] * 5
    assert False in allowed[5:], "the bucket never emptied"


def test_throttling_one_caller_does_not_affect_another():
    """One tenant's runaway loop must not spend everyone else's budget."""
    rl = RateLimiter(rate_per_second=0.001, burst=2)
    rl.check("noisy"), rl.check("noisy"), rl.check("noisy")
    assert rl.check("noisy")[0] is False
    assert rl.check("quiet")[0] is True, "buckets are shared across callers"


def test_a_throttled_caller_is_told_when_to_come_back():
    rl = RateLimiter(rate_per_second=2.0, burst=1)
    rl.check("k")
    allowed, retry_after = rl.check("k")
    assert allowed is False
    assert 0 < retry_after <= 1.0, f"implausible Retry-After: {retry_after}"


def test_the_bucket_refills():
    import time
    rl = RateLimiter(rate_per_second=100.0, burst=1)
    assert rl.check("k")[0] is True
    assert rl.check("k")[0] is False
    time.sleep(0.05)
    assert rl.check("k")[0] is True, "the bucket never refilled"


# ------------------------------------------------------------------------- health
def test_liveness_does_not_depend_on_the_database(analytics_client):
    """A database blip must not make the orchestrator restart every replica.

    Liveness answers "is this process working"; a failure means *restart me*. Wiring it
    to the database turns a thirty-second outage into a cluster-wide restart storm.
    """
    import inspect

    from cp_common import health
    source = inspect.getsource(health.install_health)
    live_body = source.split("def live(")[1].split("def ready(")[0]
    for forbidden in ("SessionLocal", "database_probe", "execute("):
        assert forbidden not in live_body, \
            f"liveness touches the database ({forbidden}); readiness is the right place"

    r = analytics_client.get("/health/live")
    assert r.status_code == 200 and r.json()["status"] == "alive"


def test_readiness_does_depend_on_the_database(analytics_client):
    r = analytics_client.get("/health/ready")
    assert r.status_code == 200, r.text
    assert "database" in r.json()["checks"]


def test_readiness_fails_closed_when_a_dependency_is_down():
    """503 is what removes a replica from the load balancer's rotation."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from cp_common.health import install_health

    def broken() -> None:
        raise RuntimeError("connection refused")

    app = FastAPI()
    install_health(app, "probe-test", readiness_checks={"database": broken})
    with TestClient(app) as c:
        assert c.get("/health/live").status_code == 200, "liveness should be unaffected"
        r = c.get("/health/ready")
        assert r.status_code == 503, "an unreachable dependency must fail readiness"
        assert r.json()["status"] == "not_ready"


def test_diagnostics_never_break_readiness():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from cp_common.health import install_health

    def exploding_details() -> dict:
        raise RuntimeError("diagnostics blew up")

    app = FastAPI()
    install_health(app, "probe-test", readiness_checks={}, details=exploding_details)
    with TestClient(app) as c:
        assert c.get("/health/ready").status_code == 200


# ------------------------------------------------------------ gateway wiring order
def test_unauthenticated_traffic_is_rate_limited_too():
    """Found by load-testing the gateway, not by reading it.

    The first implementation rejected a missing token before consulting the limiter, so
    a token-less flood was never accounted for at all - the one kind of traffic most
    worth throttling was the only kind exempt. The rejection now happens after the
    request has been counted.
    """
    from fastapi.testclient import TestClient

    from services.gateway.app.main import app, limiter

    src = (__import__("pathlib").Path(__file__).resolve().parents[1]
           / "services/gateway/app/main.py").read_text(encoding="utf-8")
    body = src.split("async def proxy(", 1)[1]
    limit_at = body.index("limiter.check(")
    reject_at = body.index('content={"error": {"message": auth_error}}')
    assert limit_at < reject_at, \
        "the 401 is returned before the rate limit is applied; floods bypass the limiter"

    rate, burst = limiter.rate_per_second, limiter.burst
    # A fast refill rate makes this race the test runner: at 1000/s a token is returned
    # in the ~1ms each request takes, so whether the bucket ever empties depends on how
    # busy the machine is. Refill effectively off, so the outcome is the behaviour.
    limiter.rate_per_second, limiter.burst = 0.001, 5
    for key in list(limiter._buckets):
        limiter.forget(key)
    try:
        with TestClient(app) as c:
            codes = [c.get("/api/analytics/t/views").status_code for _ in range(12)]
    finally:
        limiter.rate_per_second, limiter.burst = rate, burst
        for key in list(limiter._buckets):
            limiter.forget(key)

    assert codes.count(401) == 5, f"expected exactly the burst through, got {codes}"
    assert codes.count(429) == 7, f"the flood was not throttled: {codes}"


def test_every_response_carries_a_correlation_id():
    """Including the failures - a 401 you cannot trace is a 401 you cannot debug."""
    from fastapi.testclient import TestClient

    from services.gateway.app.main import app, limiter

    limiter.rate_per_second, limiter.burst = 1000.0, 1000
    for key in list(limiter._buckets):
        limiter.forget(key)
    with TestClient(app) as c:
        for path, expected in (("/api/analytics/t/views", 401), ("/api/nosuch/x", 404)):
            r = c.get(path)
            assert r.status_code == expected
            assert r.headers.get("x-request-id"), f"{path} has no correlation id"

        supplied = c.get("/api/analytics/t/views", headers={"x-request-id": "trace-abc"})
        assert supplied.headers["x-request-id"] == "trace-abc", \
            "a client-supplied trace id was discarded"
