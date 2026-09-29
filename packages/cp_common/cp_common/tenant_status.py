"""Is this tenant still entitled to be served? (BR-107)

Suspension was gating the console login and nothing else. A suspended bank's staff could
not sign in, while its payment switch carried on posting transactions and its channel
carried on asking for inline decisions. That is not a suspension — it is a locked front
door on a building whose loading bay is still open, and for a commercial control it is
the wrong half: the machine path is where the volume, and the billing, actually is.

The tenant register lives in tenant-service, and the intake and decision services do not
have a grant on that schema, by design. So status is fetched over the internal API and
cached for a few seconds — a switch calls intake thousands of times a second and cannot
have a cross-service hop on each one.

**On staleness.** The cache means a suspension takes effect within ``TTL_SECONDS`` rather
than instantly, which is stated here rather than glossed. When tenant-service cannot be
reached, the last known status is used and marked stale; if nothing was ever known, the
tenant is **served**, not refused. That direction is deliberate and is the one place in
this file where we fail open: a tenant-service outage must not stop a bank's payments,
and the failure mode of wrongly serving a suspended tenant for a few minutes is a billing
dispute, while the failure mode of wrongly refusing an active one is a payment incident.
The choice is recorded on the result so it is auditable rather than assumed.
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass

import httpx

from .errors import AppError
from .settings import settings

#: Short enough that a suspension bites promptly, long enough that a switch at several
#: thousand transactions a second is not generating that many internal calls.
TTL_SECONDS = 15.0

#: Statuses that may not be served. ``degraded`` is explicitly serviceable — it means we
#: are having trouble, not that the tenant has done anything.
NOT_SERVED = {"suspended", "offboarded"}

#: Why a tenant is not being served, phrased for the integration engineer who will
#: read it in a 403 body at three in the morning.
WHY_NOT_SERVED = {
    "suspended": ("This tenant is suspended. Transaction intake and inline decisioning "
                  "are stopped; the fraud record is retained and is exportable."),
    "offboarded": ("This tenant is offboarded. The service is closed. The fraud record "
                   "remains exportable until the agreed retention date."),
}

_lock = threading.Lock()
_cache: dict[str, tuple[float, str, bool]] = {}


@dataclass(frozen=True)
class TenantStanding:
    status: str
    served: bool
    #: True when tenant-service could not be reached and this is a remembered answer.
    stale: bool
    reason: str
    #: BR-109: a self-service sandbox, never promoted. Fetched alongside status from the
    #: same call and cached the same way - callers that care (ingestion's sandbox
    #: isolation check) pay no extra round trip for it. Defaults False on the
    #: never-seen/unreachable path, same direction status's own "serve by default" takes:
    #: an unknown tenant is not assumed to be a sandbox any more than it is assumed
    #: suspended.
    is_sandbox: bool = False


def _fetch(tenant_id: str) -> tuple[str, bool]:
    r = httpx.get(
        f"{settings.tenant_service_url}/tenants/internal/{tenant_id}/identity",
        headers={"x-internal-key": settings.internal_api_key}, timeout=3.0)
    r.raise_for_status()
    body = r.json()
    return str(body.get("status") or ""), bool(body.get("is_sandbox", False))


def standing(tenant_id: str, *, now: float | None = None) -> TenantStanding:
    """The tenant's lifecycle standing, cached, with staleness disclosed."""
    now = now if now is not None else time.monotonic()
    with _lock:
        hit = _cache.get(tenant_id)
    if hit and now - hit[0] < TTL_SECONDS:
        status, is_sandbox = hit[1], hit[2]
        stale = False
    else:
        try:
            status, is_sandbox = _fetch(tenant_id)
            with _lock:
                _cache[tenant_id] = (now, status, is_sandbox)
            stale = False
        except Exception:  # noqa: BLE001
            if hit:
                status, is_sandbox, stale = hit[1], hit[2], True
            else:
                # Never seen this tenant and cannot ask. Serve, and say why.
                return TenantStanding(
                    status="unknown", served=True, stale=True,
                    reason="Tenant lifecycle status could not be checked and no previous "
                           "answer is held. Serving, because refusing on a status-lookup "
                           "failure would turn a tenant-service outage into a payments "
                           "outage.")

    served = status not in NOT_SERVED
    reason = WHY_NOT_SERVED.get(status, "") if not served else ""
    if stale and reason:
        reason += " (Status is a cached answer; tenant-service was unreachable.)"
    return TenantStanding(status=status, served=served, stale=stale, reason=reason,
                          is_sandbox=is_sandbox)


def require_served(tenant_id: str) -> TenantStanding:
    """Raise 403 unless this tenant may be served right now."""
    st = standing(tenant_id)
    if not st.served:
        raise AppError(st.reason, 403, f"tenant_{st.status}")
    return st


def forget(tenant_id: str | None = None) -> None:
    """Drop cached standing, so a resume takes effect at once rather than after the TTL."""
    with _lock:
        if tenant_id is None:
            _cache.clear()
        else:
            _cache.pop(tenant_id, None)
