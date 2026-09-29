"""Notification-service entrypoint."""
from fastapi import FastAPI

from cp_common import (
    IdempotencyMiddleware, database_probe, install_error_handlers, install_health,
    install_observability, settings,
)

from .routes import notify

app = FastAPI(title="Notification Service", version="0.1.0")
install_observability(app, "notification-service", internal_key=settings.internal_api_key)
install_error_handlers(app)
app.add_middleware(IdempotencyMiddleware)
app.include_router(notify.router)


@app.get("/health", tags=["meta"])
def health() -> dict:
    return {"status": "ok", "service": "notification-service"}


install_health(app, "notification-service", readiness_checks={"database": database_probe})
