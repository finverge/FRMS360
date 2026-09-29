"""Decision-service entrypoint — Lane A, the inline decision path.

A separate deployable because its availability class differs from everything else. In
the payment path, this service being down means payments fail; the analytics tier being
down means a dashboard is empty. Those cannot share a scaling policy, a deployment
window or an SLA, and putting them in one process would tie them together permanently.
"""
import logging

from fastapi import FastAPI

from cp_common import (
    database_probe, install_error_handlers, install_health, install_observability,
    settings,
)

from . import store
from .routes import decide, internal, review

log = logging.getLogger("decision.main")

app = FastAPI(title="Decision Service", version="0.1.0")
install_observability(app, "decision-service", internal_key=settings.internal_api_key)
install_error_handlers(app)
app.include_router(decide.router)
app.include_router(internal.router)
app.include_router(review.router)


@app.on_event("startup")
def _warm_catalogues() -> None:
    """Load every tenant's catalogue before the first payment arrives.

    Without this the first decision for each tenant is unscreened — correctly reported as
    ``not_screened``, but still a payment nobody checked. Warming here trades a slower
    start-up for that, which is the right way round. A failure is logged and start-up
    continues: refusing to boot because config-service is briefly unavailable would turn a
    dependency blip into a payments outage.
    """
    import httpx
    try:
        r = httpx.get(f"{settings.tenant_service_url}/tenants/internal/active",
                      headers={"x-internal-key": settings.internal_api_key}, timeout=10.0)
        r.raise_for_status()
        tenants = r.json().get("tenant_ids", [])
    except Exception as exc:  # noqa: BLE001
        log.warning("could not list tenants to warm the rule catalogue: %s. Catalogues "
                    "load on first use instead, and the first payment per tenant is "
                    "reported as not screened.", str(exc)[:200])
        return
    loaded = store.preload(tenants)
    log.info("warmed inline catalogues: %s", loaded)


@app.get("/health", tags=["meta"])
def health() -> dict:
    from .routes.decide import WRITE_FAILURES
    body = {"status": "ok", "service": "decision-service", "config": store.status()}
    # A lane that cannot write its decision log is not healthy, whatever it returns to
    # callers. Surfaced here so a probe sees it without anyone reading the report.
    if WRITE_FAILURES["count"]:
        body["status"] = "degraded"
        body["decision_log_writes_failing"] = dict(WRITE_FAILURES)
    return body


install_health(app, "decision-service", readiness_checks={"database": database_probe})
