"""Metrics and structured request logging.

Written against the Prometheus text exposition format directly rather than pulling in a
client library. The format is small and stable, and a bank's change board asks about every
dependency in a deployment - one fewer is worth a hundred lines.

Four decisions here are deliberate, and each is a way metrics normally go wrong:

* **No ``tenant_id`` label, ever.** It is the most tempting label in a multi-tenant
  platform and the most damaging: five hundred tenants times fifty routes times five
  status classes is a hundred and twenty-five thousand time series, and the scrape output
  becomes a list of the customers. Per-tenant detail belongs in the structured logs, which
  are access-controlled. Metrics are for the shape of the system, not the shape of the
  book.

* **Route templates, not paths.** ``/analytics/{tenant_id}/cases/{case_id}`` is one series;
  the raw path would mint a new one per case and eventually take the scrape down with it.

* **Buckets chosen for the SLO.** The defaults most libraries ship top out around ten
  seconds, which puts every interesting request in ``+Inf``. These are set where this
  platform's answers actually live.

* **Freshness is a first-class metric.** A p99 of 20ms means nothing if detection stopped
  six hours ago. ``pipeline_lag_seconds`` is the number an on-call engineer should be
  paged on, and it is the one a generic HTTP dashboard will never show.
"""
from __future__ import annotations

import json
import logging
import time
import uuid
from collections import defaultdict
from contextvars import ContextVar
from threading import Lock
from typing import Iterable

# Imported at module scope, not inside the installer: FastAPI resolves a route's type
# annotations against the defining module's globals, so a function-local ``Request``
# leaves it unable to build the schema.
from fastapi import Request
from fastapi.responses import PlainTextResponse

log = logging.getLogger("cp.access")

#: Propagated from the gateway so one user action is traceable across every service.
request_id_var: ContextVar[str] = ContextVar("request_id", default="")
tenant_var: ContextVar[str] = ContextVar("tenant", default="")

#: Seconds. Deliberately dense below one second - a dashboard query and a case transition
#: both live there - and stretched out to a minute for the batch endpoints.
LATENCY_BUCKETS = (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0,
                   60.0)


def _fmt(value: float) -> str:
    """Prometheus wants integers without a trailing .0 and no scientific notation."""
    if value == int(value) and abs(value) < 1e15:
        return str(int(value))
    return repr(float(value))


def _labels(pairs: tuple[tuple[str, str], ...]) -> str:
    if not pairs:
        return ""
    inner = ",".join(
        f'{k}="{str(v).replace(chr(92), chr(92) * 2).replace(chr(34), chr(92) + chr(34))}"'
        for k, v in pairs)
    return "{" + inner + "}"


class _Metric:
    def __init__(self, name: str, help_text: str, kind: str,
                 labelnames: tuple[str, ...] = ()):
        self.name = name
        self.help = help_text
        self.kind = kind
        self.labelnames = labelnames
        self._lock = Lock()

    def _key(self, labels: dict) -> tuple[tuple[str, str], ...]:
        missing = set(self.labelnames) - set(labels)
        if missing:
            raise ValueError(f"{self.name} missing labels: {sorted(missing)}")
        return tuple((k, str(labels[k])) for k in self.labelnames)


class Counter(_Metric):
    def __init__(self, name, help_text, labelnames=()):
        super().__init__(name, help_text, "counter", labelnames)
        self._values: dict[tuple, float] = defaultdict(float)

    def inc(self, amount: float = 1.0, **labels) -> None:
        with self._lock:
            self._values[self._key(labels)] += amount

    def render(self) -> Iterable[str]:
        yield f"# HELP {self.name} {self.help}"
        yield f"# TYPE {self.name} counter"
        with self._lock:
            items = list(self._values.items())
        for key, v in items:
            yield f"{self.name}_total{_labels(key)} {_fmt(v)}"


class Gauge(_Metric):
    def __init__(self, name, help_text, labelnames=()):
        super().__init__(name, help_text, "gauge", labelnames)
        self._values: dict[tuple, float] = {}

    def set(self, value: float, **labels) -> None:
        with self._lock:
            self._values[self._key(labels)] = float(value)

    def inc(self, amount: float = 1.0, **labels) -> None:
        """For concurrency gauges, where set() is simply wrong.

        Two overlapping requests both setting 1 and then the first setting 0 reports an
        idle service while the second is still running.
        """
        with self._lock:
            key = self._key(labels)
            self._values[key] = self._values.get(key, 0.0) + amount

    def dec(self, amount: float = 1.0, **labels) -> None:
        self.inc(-amount, **labels)

    def render(self) -> Iterable[str]:
        yield f"# HELP {self.name} {self.help}"
        yield f"# TYPE {self.name} gauge"
        with self._lock:
            items = list(self._values.items())
        for key, v in items:
            yield f"{self.name}{_labels(key)} {_fmt(v)}"


class Histogram(_Metric):
    def __init__(self, name, help_text, labelnames=(), buckets=LATENCY_BUCKETS):
        super().__init__(name, help_text, "histogram", labelnames)
        self.buckets = tuple(sorted(buckets))
        self._counts: dict[tuple, list[int]] = {}
        self._sum: dict[tuple, float] = defaultdict(float)
        self._total: dict[tuple, int] = defaultdict(int)

    def observe(self, value: float, **labels) -> None:
        key = self._key(labels)
        with self._lock:
            counts = self._counts.setdefault(key, [0] * len(self.buckets))
            for i, edge in enumerate(self.buckets):
                if value <= edge:
                    counts[i] += 1
            self._sum[key] += value
            self._total[key] += 1

    def render(self) -> Iterable[str]:
        yield f"# HELP {self.name} {self.help}"
        yield f"# TYPE {self.name} histogram"
        with self._lock:
            keys = list(self._counts)
            snapshot = {k: (list(self._counts[k]), self._sum[k], self._total[k])
                        for k in keys}
        for key, (counts, total_sum, n) in snapshot.items():
            for edge, c in zip(self.buckets, counts):
                yield (f"{self.name}_bucket"
                       f"{_labels(key + (('le', _fmt(edge)),))} {c}")
            yield f"{self.name}_bucket{_labels(key + (('le', '+Inf'),))} {n}"
            yield f"{self.name}_sum{_labels(key)} {_fmt(total_sum)}"
            yield f"{self.name}_count{_labels(key)} {n}"


class Registry:
    def __init__(self):
        self._metrics: list[_Metric] = []

    def register(self, metric: _Metric):
        self._metrics.append(metric)
        return metric

    def render(self) -> str:
        out: list[str] = []
        for m in self._metrics:
            out.extend(m.render())
        return "\n".join(out) + "\n"


REGISTRY = Registry()

# ---- RED: rate, errors, duration -------------------------------------------------
REQUESTS = REGISTRY.register(Counter(
    "cp_http_requests", "HTTP requests handled.",
    ("service", "method", "route", "status")))
LATENCY = REGISTRY.register(Histogram(
    "cp_http_request_duration_seconds", "Request duration.",
    ("service", "method", "route")))
IN_FLIGHT = REGISTRY.register(Gauge(
    "cp_http_requests_in_flight", "Requests currently being served.", ("service",)))

# ---- the numbers worth being paged for -------------------------------------------
PIPELINE_LAG = REGISTRY.register(Gauge(
    "cp_pipeline_lag_seconds",
    "Age of the oldest item still waiting at this stage. The metric to alert on: a "
    "healthy p99 means nothing if nothing has been processed for six hours.",
    ("service", "stage")))
QUEUE_DEPTH = REGISTRY.register(Gauge(
    "cp_queue_depth", "Items waiting at this stage.", ("service", "stage")))
BUILD_INFO = REGISTRY.register(Gauge(
    "cp_build_info", "Always 1; the labels carry the detail.",
    ("service", "version")))


def _route_of(request) -> str:
    """The templated path, so ids do not each mint their own time series."""
    route = request.scope.get("route")
    path = getattr(route, "path", None)
    if path:
        return path
    # No matched route means a 404. Bucketed as one series rather than echoing whatever
    # a scanner asked for, which would otherwise be an unbounded label source.
    return "__unmatched__"


def install_observability(app, service: str, *, version: str = "0.1.0",
                          internal_key: str | None = None):
    """Add RED metrics, structured access logging and a ``/metrics`` endpoint."""
    BUILD_INFO.set(1, service=service, version=version)

    @app.middleware("http")
    async def _observe(request: Request, call_next):
        rid = request.headers.get("x-request-id") or uuid.uuid4().hex[:16]
        token = request_id_var.set(rid)
        ttoken = tenant_var.set("")
        IN_FLIGHT.inc(service=service)
        started = time.perf_counter()
        status = 500
        try:
            response = await call_next(request)
            status = response.status_code
            response.headers["x-request-id"] = rid
            return response
        finally:
            elapsed = time.perf_counter() - started
            route = _route_of(request)
            # Read only now: path params are populated by the router, which runs inside
            # call_next. Reading them on the way in yields nothing at all, and the tenant
            # would have been silently absent from every access log line.
            tenant = (request.scope.get("path_params") or {}).get("tenant_id", "")
            if tenant:
                tenant_var.set(tenant)
            REQUESTS.inc(service=service, method=request.method, route=route,
                         status=str(status))
            LATENCY.observe(elapsed, service=service, method=request.method, route=route)
            IN_FLIGHT.dec(service=service)
            # One JSON object per request. Greppable, and it carries the request id the
            # gateway generated, so a user's "it failed at 10:42" is one search.
            log.info(json.dumps({
                "ts": time.time(), "service": service, "request_id": rid,
                "method": request.method, "route": route,
                "path": request.url.path, "status": status,
                "duration_ms": round(elapsed * 1000, 2),
                "tenant_id": tenant_var.get() or None,
            }))
            request_id_var.reset(token)
            tenant_var.reset(ttoken)

    @app.get("/metrics", include_in_schema=False)
    def metrics(request: Request):
        """Scrape endpoint.

        Gated behind the internal key like every other internal surface. Metrics from a
        multi-tenant platform describe the customer base - request volume by route, queue
        depths, error shapes - and are not something to serve to anyone who can reach the
        port.
        """
        if internal_key:
            supplied = request.headers.get("x-internal-key")
            if supplied != internal_key:
                return PlainTextResponse("forbidden", status_code=403)
        return PlainTextResponse(REGISTRY.render(),
                                 media_type="text/plain; version=0.0.4")

    return app
