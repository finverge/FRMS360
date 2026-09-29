"""Analytics-service entrypoint."""
import os

from fastapi import FastAPI

from cp_common import IdempotencyMiddleware, database_probe, install_error_handlers, install_health, install_observability, settings

from .routes import (
    accountability, board_pack, cases, ctr, dashboards, detection, documents,
    exit_bundle, filings, recovery, reference, subscriptions, usage, views,
)

app = FastAPI(title="Analytics Service", version="0.1.0")
install_observability(app, "analytics-service", internal_key=settings.internal_api_key)
install_error_handlers(app)
# Honoured only when the caller supplies Idempotency-Key; see
# cp_common.idempotency for why the server must not invent one.
app.add_middleware(IdempotencyMiddleware)
app.include_router(dashboards.router)
app.include_router(cases.router)
app.include_router(detection.router)
app.include_router(filings.router)
app.include_router(ctr.router)
app.include_router(accountability.router)
app.include_router(board_pack.router)
app.include_router(recovery.router)
app.include_router(reference.router)
app.include_router(usage.router)
app.include_router(views.router)
app.include_router(documents.router)
app.include_router(subscriptions.router)
app.include_router(exit_bundle.router)


@app.get("/health", tags=["meta"])
def health() -> dict:
    from .rules import cache_status

    return {
        "status": "ok",
        "service": "analytics-service",
        "engine": os.environ.get("ANALYTICS_ENGINE", "postgres"),
        # Reported so a load-balanced deployment can be inspected directly rather than
        # diagnosed from users comparing screenshots of different numbers.
        "cache": cache_status(),
    }


def _cache_details() -> dict:
    from .rules import cache_status

    return {"cache": cache_status()}


# Liveness must not touch the database; readiness must. See cp_common.health.
install_health(app, "analytics-service",
               readiness_checks={"database": database_probe},
               details=_cache_details)
