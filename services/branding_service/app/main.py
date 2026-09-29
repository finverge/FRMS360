"""Branding-service entrypoint."""
from contextlib import asynccontextmanager

from fastapi import FastAPI

from cp_common import IdempotencyMiddleware, database_probe, init_db, install_error_handlers, install_health, install_observability, settings

from .models import Base
from .routes import branding


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db(Base.metadata)
    yield


app = FastAPI(title="Branding Service", version="0.1.0", lifespan=lifespan)
install_observability(app, "branding-service", internal_key=settings.internal_api_key)
install_error_handlers(app)
# Honoured only when the caller supplies Idempotency-Key; see
# cp_common.idempotency for why the server must not invent one.
app.add_middleware(IdempotencyMiddleware)
app.include_router(branding.router)


@app.get("/health", tags=["meta"])
def health() -> dict:
    return {"status": "ok", "service": "branding-service"}


# Liveness must not touch the database; readiness must. See cp_common.health.
install_health(app, "branding-service",
               readiness_checks={"database": database_probe})
