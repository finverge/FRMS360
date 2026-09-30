"""API gateway — the single edge for the control plane.

Responsibilities:
  * serve the admin console (static SPA),
  * terminate auth at the edge (verify the JWT for protected routes, fail fast),
  * route /api/* to the owning microservice,
  * inject the resolved tenant context header for downstream services.

It is intentionally thin: business rules live in the services, not here.
"""
import asyncio
import logging
import uuid
from pathlib import Path

import httpx
from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from cp_common import decode_token, install_health, settings

from .resilience import (
    CircuitBreaker, CircuitOpen, RateLimiter, backoff_seconds, should_retry,
)

log = logging.getLogger("gateway")

# path prefix -> (upstream base url, upstream path prefix)
ROUTES = {
    "auth": (settings.tenant_service_url, "/auth"),
    "machine": (settings.tenant_service_url, "/machine"),
    "tenants": (settings.tenant_service_url, "/tenants"),
    "audit": (settings.tenant_service_url, "/audit"),
    "branding": (settings.branding_service_url, "/branding"),
    "configs": (settings.config_service_url, "/configs"),
    "analytics": (settings.analytics_service_url, "/analytics"),
    "ingest": (settings.ingestion_service_url, "/ingest"),
    # Reporting only. /decide is not proxied: the payment path is a direct,
    # machine-authenticated call and must not inherit the console's hop.
    "decisions": (settings.decision_service_url, "/decisions"),
    "notifications": (settings.notification_service_url, "/notifications"),
    "lane-c": (settings.lane_c_service_url, "/lane-c"),
}

HOP_BY_HOP = {"host", "content-length", "transfer-encoding", "connection", "keep-alive"}

app = FastAPI(title="Control Plane Gateway", version="0.1.0")

# Timeouts are split rather than one flat number. A connect that has not completed in two
# seconds is a dead upstream and waiting thirty proves nothing; a read may legitimately
# take longer because some analytical queries are genuinely slow. The pool is bounded so
# one slow upstream cannot consume every connection the gateway has - the specific way a
# single degraded service used to take the whole console down.
client = httpx.AsyncClient(
    timeout=httpx.Timeout(connect=2.0, read=25.0, write=10.0, pool=3.0),
    limits=httpx.Limits(max_connections=100, max_keepalive_connections=20),
)

# AI Insights calls a real, locally self-hosted LLM (see analytics_service's
# ai_insights.py) which can take well over 25s to even start responding on a cold
# model load - observed directly on this host. This is a per-route override, not a
# blanket bump of the client default above: an ordinary endpoint that hangs should
# still fail fast at 25s, not make the console wait three minutes to find out.
SLOW_ROUTE_TIMEOUT = httpx.Timeout(connect=2.0, read=185.0, write=10.0, pool=3.0)


def _timeout_for(path: str):
    if "/ai-insights/" in path:
        return SLOW_ROUTE_TIMEOUT
    return httpx.USE_CLIENT_DEFAULT

#: One breaker per upstream, so a failing analytics-service cannot deny service to auth.
BREAKERS = {name: CircuitBreaker(name) for name in
            ("tenants", "auth", "machine", "audit", "branding", "configs", "analytics", "ingest",
             "notifications", "lane-c")}

limiter = RateLimiter(rate_per_second=settings.rate_limit_per_second,
                      burst=settings.rate_limit_burst)

MAX_ATTEMPTS = 3


def _is_public(segment: str, path: str) -> bool:
    if segment == "auth":  # login is public
        return True
    if segment == "branding" and (path.endswith("theme.css") or path.endswith("theme.json")):
        return True
    # BR-109: a prospective bank provisions its own sandbox with no prior login - see
    # tenant_service.routes.tenants.self_service_signup for what keeps this safe
    # (it is not this auth check). Every other /tenants path stays behind a token.
    if segment == "tenants" and path == "tenants/self-service":
        return True
    return False


@app.api_route("/api/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
async def proxy(path: str, request: Request) -> Response:
    # Generated before anything can fail, so every response - including a 401 or a 404 -
    # can be tied back to its log lines. One id follows a request across every replica it
    # touches; without it, correlating the gateway with the service that actually served
    # the query means guessing from timestamps.
    request_id = request.headers.get("x-request-id") or uuid.uuid4().hex[:16]
    rid = {"x-request-id": request_id}

    segment = path.split("/", 1)[0]
    route = ROUTES.get(segment)
    if not route:
        return JSONResponse(status_code=404, headers=rid,
                            content={"error": {"message": "unknown route"}})

    # The token is resolved before the rate limit is applied, but a bad token is not
    # rejected until after it. Rejecting first looks tidier and leaves the gateway wide
    # open: an unauthenticated flood would never reach the limiter at all, and refusing a
    # request without accounting for it is exactly what makes that flood cheap to send.
    principal_tenant = None
    principal_sub = None
    auth_error = None
    if not _is_public(segment, path):
        auth = request.headers.get("authorization", "")
        if not auth.lower().startswith("bearer "):
            auth_error = "missing token"
        else:
            try:
                claims = decode_token(auth.split(" ", 1)[1])
                principal_tenant = claims.get("tenant_id")
                principal_sub = claims.get("sub")
            except Exception:
                auth_error = "invalid token"

    # Keyed on the caller, not the path: one runaway loop must not be able to spend
    # everyone else's budget. Unauthenticated traffic shares a bucket per source address.
    caller = principal_sub or (request.client.host if request.client else "anonymous")
    allowed, retry_after = limiter.check(caller)
    if not allowed:
        return JSONResponse(
            status_code=429, headers={"Retry-After": f"{retry_after:.3f}", **rid},
            content={"error": {"message": "Too many requests. Slow down and retry.",
                               "code": "rate_limited"}})

    if auth_error:
        return JSONResponse(status_code=401, headers=rid,
                            content={"error": {"message": auth_error}})

    base, prefix = route
    rest = path[len(segment):]  # includes leading slash or ""
    upstream = f"{base}{prefix}{rest}"

    fwd_headers = {k: v for k, v in request.headers.items() if k.lower() not in HOP_BY_HOP}
    if principal_tenant:
        fwd_headers["x-tenant-id"] = principal_tenant  # resolved tenant context
    fwd_headers["x-request-id"] = request_id

    body = await request.body()
    breaker = BREAKERS.get(segment)

    upstream_resp = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            if breaker:
                breaker.before_request()
        except CircuitOpen as exc:
            log.warning("circuit open for %s (request %s)", segment, request_id)
            return JSONResponse(
                status_code=503,
                headers={"Retry-After": f"{max(1, int(exc.retry_after))}",
                         "x-request-id": request_id},
                content={"error": {
                    "message": f"The {segment} service is temporarily unavailable.",
                    "code": "upstream_unavailable"}})

        connect_error = False
        try:
            upstream_resp = await client.request(
                request.method, upstream, headers=fwd_headers,
                # multi_items(), not the QueryParams mapping itself. httpx iterates a
                # mapping with .items(), and Starlette's .items() keeps only the last
                # value for a repeated key - so every multi-select filter in the console
                # (rails, families, severities, regions, products, segments, states,
                # fmr_categories, dispositions) silently narrowed to whichever value
                # happened to be last. Selecting UPI and NEFT filtered on NEFT alone, and
                # the screen looked entirely plausible.
                params=list(request.query_params.multi_items()), content=body,
                timeout=_timeout_for(upstream))
        except (httpx.ConnectError, httpx.ConnectTimeout, httpx.ReadTimeout,
                httpx.PoolTimeout) as exc:
            connect_error = True
            upstream_resp = None
            log.warning("upstream %s failed (attempt %s/%s, request %s): %s",
                        segment, attempt, MAX_ATTEMPTS, request_id, exc)

        status = upstream_resp.status_code if upstream_resp is not None else None
        failed = connect_error or (status is not None and status in (502, 503, 504))
        if breaker:
            breaker.record_failure() if failed else breaker.record_success()

        # A write is retried only if the caller labelled it replayable.
        if not should_retry(request.method, attempt, MAX_ATTEMPTS, status, connect_error,
                            idempotency_key=request.headers.get("idempotency-key", "")):
            break
        await asyncio.sleep(backoff_seconds(attempt))

    if upstream_resp is None:
        return JSONResponse(
            status_code=502, headers={"x-request-id": request_id},
            content={"error": {"message": f"{segment} service unavailable",
                               "code": "upstream_unreachable"}})

    resp_headers = {k: v for k, v in upstream_resp.headers.items() if k.lower() not in HOP_BY_HOP}
    resp_headers["x-request-id"] = request_id
    return Response(
        content=upstream_resp.content,
        status_code=upstream_resp.status_code,
        headers=resp_headers,
        media_type=upstream_resp.headers.get("content-type"),
    )


@app.get("/health", tags=["meta"])
def health() -> dict:
    return {"status": "ok", "service": "gateway", "platform": settings.platform_name}


class NoCacheStatic(StaticFiles):
    """Serve console assets with revalidation forced.

    Without this the browser keeps a stale app.js after a deploy: index.html may be
    re-fetched while the script tag's URL is unchanged, so the page silently runs old
    code. That cost real debugging time and made every change need a manual hard refresh.
    The vendored chart library is immutable, so it keeps a long cache.
    """

    async def get_response(self, path: str, scope):
        response = await super().get_response(path, scope)
        if path.startswith("vendor/"):
            response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        else:
            response.headers["Cache-Control"] = "no-cache, must-revalidate"
        return response


# The gateway holds no database role, so its readiness is about whether it can still
# reach upstreams: an open circuit on every route means this replica is serving nothing.
def _upstreams_probe() -> None:
    open_circuits = [b.name for b in BREAKERS.values() if b.state == "open"]
    if len(open_circuits) == len(BREAKERS):
        raise RuntimeError("all upstreams unavailable: " + ", ".join(open_circuits))


install_health(app, "gateway", readiness_checks={"upstreams": _upstreams_probe},
               details=lambda: {"circuits": [b.snapshot() for b in BREAKERS.values()]})


# Serve the admin console. Mounted last so /api and /health win.
_static = Path(__file__).parent / "static"
app.mount("/", NoCacheStatic(directory=str(_static), html=True), name="console")
