"""Liveness and readiness, which are not the same question.

A load balancer and an orchestrator ask different things, and answering both with one
``/health`` endpoint causes real outages:

``/health/live``  - "is this process working?" A failure here means *restart me*. It must
                    NOT depend on the database. If it does, a thirty-second database blip
                    fails liveness on every replica at once, the orchestrator restarts all
                    of them, and a brief dependency wobble becomes a full outage - with
                    cold pools and a thundering herd on the database that just recovered.

``/health/ready`` - "should traffic be sent here *now*?" A failure means *stop routing to
                    me*, nothing more. This one does check dependencies: a replica that
                    cannot reach its database should be taken out of rotation rather than
                    returning errors to users.

The distinction only pays off when something is actually wrong, which is exactly when
nobody has time to reason it out. So it is settled here, once.
"""
from __future__ import annotations

import time
from typing import Callable

from fastapi import FastAPI
from fastapi.responses import JSONResponse

_STARTED_AT = time.time()


def install_health(app: FastAPI, service: str, *,
                   readiness_checks: dict[str, Callable[[], None]] | None = None,
                   details: Callable[[], dict] | None = None) -> None:
    """Add /health, /health/live and /health/ready to a service.

    ``readiness_checks`` maps a dependency name to a callable that raises when unhealthy.
    """
    checks = readiness_checks or {}

    @app.get("/health/live", tags=["meta"])
    def live() -> dict:
        # Deliberately dependency-free. If this process can execute this function it is
        # alive; whether it can do useful work is the readiness question.
        return {"status": "alive", "service": service,
                "uptime_seconds": round(time.time() - _STARTED_AT, 1)}

    @app.get("/health/ready", tags=["meta"])
    def ready():
        results, failures = {}, []
        for name, probe in checks.items():
            try:
                probe()
                results[name] = "ok"
            except Exception as exc:  # noqa: BLE001
                results[name] = f"failed: {type(exc).__name__}"
                failures.append(name)
        body = {"status": "ready" if not failures else "not_ready",
                "service": service, "checks": results}
        if details:
            try:
                body["details"] = details()
            except Exception:  # noqa: BLE001
                # Diagnostics must never be the reason readiness fails.
                body["details"] = {}
        if failures:
            # 503 is what makes a load balancer remove this replica from rotation.
            return JSONResponse(status_code=503, content=body)
        return body


def database_probe() -> None:
    """Readiness probe: can this replica actually reach its own schema?"""
    from sqlalchemy import text

    from .db import SessionLocal
    db = SessionLocal()
    try:
        db.execute(text("SELECT 1"))
    finally:
        db.close()
