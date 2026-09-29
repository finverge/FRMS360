"""Tenant-service entrypoint."""
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from cp_common import IdempotencyMiddleware, database_probe, init_db, install_error_handlers, install_health, install_observability, settings

from .models import Base
from .routes import audit, auth, sso, tenants, machine
from .seed import run_seed

log = logging.getLogger("tenant-service")


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db(Base.metadata)  # dev-only create_all (guarded by AUTO_CREATE_TABLES)
    run_seed()
    log.info("tenant-service ready")
    yield


app = FastAPI(title="Tenant Service", version="0.1.0", lifespan=lifespan)
install_observability(app, "tenant-service", internal_key=settings.internal_api_key)
install_error_handlers(app)
# Honoured only when the caller supplies Idempotency-Key; see
# cp_common.idempotency for why the server must not invent one.
app.add_middleware(IdempotencyMiddleware)
app.include_router(auth.router)
app.include_router(machine.router)
app.include_router(sso.router)
app.include_router(tenants.router)
app.include_router(audit.router)


@app.get("/health", tags=["meta"])
def health() -> dict:
    return {"status": "ok", "service": "tenant-service", "platform": settings.platform_name}


# Liveness must not touch the database; readiness must. See cp_common.health.
install_health(app, "tenant-service",
               readiness_checks={"database": database_probe})
