"""Lane C service entrypoint."""
from contextlib import asynccontextmanager

from fastapi import FastAPI

from cp_common import database_probe, init_db, install_error_handlers, install_health, install_observability, settings

from .models import Base
from .routes import lane_c


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db(Base.metadata)
    yield


app = FastAPI(title="Lane C Service", version="0.1.0", lifespan=lifespan)
install_observability(app, "lane-c-service", internal_key=settings.internal_api_key)
install_error_handlers(app)
app.include_router(lane_c.router)


@app.get("/health", tags=["meta"])
def health() -> dict:
    return {"status": "ok", "service": "lane-c-service"}


install_health(app, "lane-c-service", readiness_checks={"database": database_probe})
