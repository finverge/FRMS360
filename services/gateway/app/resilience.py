"""Failure isolation at the edge.

The gateway proxied with a single ``httpx.AsyncClient``, a flat 30-second timeout, no
retry, no circuit breaker and no rate limit. That is fine while every upstream is healthy
and useless the moment one is not: a slow analytics-service holds connections for thirty
seconds each, the pool fills, and requests for *branding* and *auth* - services that are
perfectly healthy - queue behind it. One degraded upstream takes the whole console down.
That is the failure this module exists to prevent.

Three mechanisms, each doing one job:

**Circuit breaker** - per upstream. After enough consecutive failures the breaker opens
and further calls to that upstream fail immediately with 503 instead of occupying a
connection for the full timeout. After a cool-down one request is allowed through to test
recovery (half-open); success closes the breaker, failure re-opens it. The point is not to
be clever about the broken service, it is to stop it consuming resources the *working*
services need.

**Retry** - safe by method, or by contract. Reads are always retryable. A write is retried
only when the caller supplied an ``Idempotency-Key``, which is the client stating that a
duplicate delivery must be collapsed rather than executed twice; the receiving service
honours that key and replays its original response instead of acting again (see
cp_common.idempotency). Without such a key a write is still never retried, because a
retried write whose first attempt already committed is how one Fraud Monitoring Return
becomes two.

**Rate limit** - a token bucket per principal. Protects upstreams from one tenant's
runaway loop, and gives an honest 429 with Retry-After rather than a timeout.

All three are in-process. With several gateway replicas the effective rate limit is
per-replica, which is documented rather than hidden - see ``RateLimiter``.
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field


class CircuitOpen(Exception):
    """The upstream is known to be failing; fail now rather than wait for a timeout."""

    def __init__(self, name: str, retry_after: float):
        super().__init__(f"{name} is unavailable")
        self.name = name
        self.retry_after = retry_after


@dataclass
class CircuitBreaker:
    """One breaker per upstream service.

    States: closed (normal), open (failing fast), half-open (one probe allowed).
    """

    name: str
    failure_threshold: int = 5
    reset_after_seconds: float = 15.0

    _failures: int = field(default=0, init=False)
    _opened_at: float = field(default=0.0, init=False)
    _half_open_in_flight: bool = field(default=False, init=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, init=False)

    @property
    def state(self) -> str:
        with self._lock:
            return self._state_locked(time.monotonic())

    def _state_locked(self, now: float) -> str:
        if self._failures < self.failure_threshold:
            return "closed"
        if now - self._opened_at >= self.reset_after_seconds:
            return "half_open"
        return "open"

    def before_request(self) -> None:
        """Raise CircuitOpen if this upstream should not be called right now."""
        now = time.monotonic()
        with self._lock:
            state = self._state_locked(now)
            if state == "open":
                raise CircuitOpen(self.name, self.reset_after_seconds - (now - self._opened_at))
            if state == "half_open":
                # Exactly one probe at a time, or a thundering herd re-breaks a service
                # at the moment it starts recovering.
                if self._half_open_in_flight:
                    raise CircuitOpen(self.name, self.reset_after_seconds)
                self._half_open_in_flight = True

    def record_success(self) -> None:
        with self._lock:
            self._failures = 0
            self._half_open_in_flight = False

    def record_failure(self) -> None:
        with self._lock:
            self._failures += 1
            self._half_open_in_flight = False
            if self._failures == self.failure_threshold:
                self._opened_at = time.monotonic()
            elif self._failures > self.failure_threshold:
                # A failed half-open probe restarts the cool-down.
                self._opened_at = time.monotonic()

    def snapshot(self) -> dict:
        with self._lock:
            return {"name": self.name, "state": self._state_locked(time.monotonic()),
                    "consecutive_failures": self._failures}


@dataclass
class RateLimiter:
    """Token bucket per key, refilled continuously.

    Deliberately in-process: a shared limiter would put a network round trip in front of
    every request to enforce a limit that exists to *save* work. The consequence is that
    with N gateway replicas a caller gets up to N x the configured rate, so the limit is
    a safety valve against runaway clients, not a billing control. Anything that must be
    exact belongs at the ingress load balancer or in a dedicated quota service.
    """

    rate_per_second: float = 20.0
    burst: int = 40

    _buckets: dict[str, tuple[float, float]] = field(default_factory=dict, init=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, init=False)

    def check(self, key: str) -> tuple[bool, float]:
        """Returns (allowed, retry_after_seconds)."""
        now = time.monotonic()
        with self._lock:
            tokens, last = self._buckets.get(key, (float(self.burst), now))
            tokens = min(self.burst, tokens + (now - last) * self.rate_per_second)
            if tokens < 1.0:
                self._buckets[key] = (tokens, now)
                return False, max(0.001, (1.0 - tokens) / self.rate_per_second)
            self._buckets[key] = (tokens - 1.0, now)
            return True, 0.0

    def forget(self, key: str) -> None:
        with self._lock:
            self._buckets.pop(key, None)


#: Status codes worth another attempt: the upstream is unreachable or explicitly
#: signalling it cannot serve right now. 500 is excluded - a genuine application error
#: will fail identically on retry and the delay helps nobody.
RETRYABLE_STATUS = frozenset({502, 503, 504})

#: Methods safe to retry with no further information.
RETRYABLE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


def should_retry(method: str, attempt: int, max_attempts: int,
                 status: int | None, connect_error: bool, *,
                 idempotency_key: str = "") -> bool:
    """Whether this attempt may be repeated.

    A write qualifies only on the strength of an Idempotency-Key. The key is a promise the
    *caller* makes and the receiving service keeps; absent one, the gateway has no way to
    know whether a lost response meant the write did not happen or merely that the answer
    did not come back, and those need opposite handling.
    """
    if attempt >= max_attempts:
        return False
    if method.upper() not in RETRYABLE_METHODS and not idempotency_key:
        return False
    if connect_error:
        return True
    return status in RETRYABLE_STATUS


def backoff_seconds(attempt: int, base: float = 0.05, cap: float = 0.5) -> float:
    """Exponential, capped. Short because a human is waiting on the other end."""
    return min(cap, base * (2 ** (attempt - 1)))
