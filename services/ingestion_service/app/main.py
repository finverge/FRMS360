"""Ingestion-service entrypoint."""
from fastapi import FastAPI

from cp_common import (
    IdempotencyMiddleware, database_probe, install_error_handlers, install_health,
    install_observability, settings,
)

from .routes import ingest

app = FastAPI(title="Ingestion Service", version="0.1.0")
install_observability(app, "ingestion-service", internal_key=settings.internal_api_key)
install_error_handlers(app)
# A bank retrying a file it already sent is normal. Dedupe on the rail's own identifier
# handles the content; this handles the request.
app.add_middleware(IdempotencyMiddleware)
app.include_router(ingest.router)


@app.get("/health", tags=["meta"])
def health() -> dict:
    return {"status": "ok", "service": "ingestion-service"}


install_health(app, "ingestion-service", readiness_checks={"database": database_probe})
