"""ISO 8583 gateway entrypoint.

Runs like every other service (``uvicorn services.iso8583_gateway.app.main:app``) so it
slots into the existing run_local.ps1 / docker-compose conventions, but the work that
matters happens on a raw asyncio TCP listener started alongside the ASGI app, not through
any HTTP route of its own - the FastAPI app here exists only for /health and /metrics,
matching every other service's observability surface.
"""
from __future__ import annotations

import logging

from fastapi import FastAPI

from cp_common import install_health, install_observability, settings

from .client import DecideClient
from .server import STATS, serve

log = logging.getLogger("iso8583_gateway.main")

app = FastAPI(title="ISO 8583 Gateway", version="0.1.0")
install_observability(app, "iso8583-gateway", internal_key=settings.internal_api_key)

_state: dict = {"tcp_server": None, "client": None}


@app.on_event("startup")
async def _start_tcp_listener() -> None:
    # Deliberately no default - see settings.py's iso8583_fail_open docstring and
    # docs/ISO8583_LANE_A_SCOPING.md §6. Refusing to start is the same discipline
    # RailPolicy.validate() already applies to every inline rail's fail mode.
    if settings.iso8583_fail_open is None:
        raise RuntimeError(
            "ISO8583_FAIL_OPEN must be set explicitly (true/false) before this gateway "
            "can start. Whether an unreachable /decide should let a card through or "
            "block it is the bank's call, not a default this gateway is willing to "
            "guess at - see docs/ISO8583_LANE_A_SCOPING.md §6.")

    client = DecideClient(
        tenant_service_url=settings.tenant_service_url,
        decision_service_url=settings.decision_service_url,
        client_id=settings.iso8583_client_id,
        client_secret=settings.iso8583_client_secret,
        tenant_id=settings.iso8583_tenant_id,
    )
    _state["client"] = client
    _state["tcp_server"] = await serve(
        host=settings.iso8583_gateway_host, port=settings.iso8583_gateway_port,
        client=client, tz_offset_minutes=settings.iso8583_tz_offset_minutes,
        fail_open=bool(settings.iso8583_fail_open))


@app.on_event("shutdown")
async def _stop_tcp_listener() -> None:
    server = _state.get("tcp_server")
    if server is not None:
        server.close()
        await server.wait_closed()
    client = _state.get("client")
    if client is not None:
        await client.aclose()


@app.get("/health", tags=["meta"])
def health() -> dict:
    return {
        "status": "ok" if _state["tcp_server"] is not None else "starting",
        "service": "iso8583-gateway",
        "listening_on": f"{settings.iso8583_gateway_host}:{settings.iso8583_gateway_port}",
        "fail_open": settings.iso8583_fail_open,
        "stats": STATS,
    }


install_health(app, "iso8583-gateway")
