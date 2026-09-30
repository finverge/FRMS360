"""Is this tenant even eligible for Lane C at all?

Lane C's entire premise is a lending relationship - a borrower, a facility, a credit
file. A Payments Bank licence cannot extend credit (see config-service's policy.py:
ENTITY_TYPES), so a PB tenant has no borrowers for Lane C to monitor, the same reasoning
``CREDIT_LINKED_RULES`` gates the EWS catalogue's own loan-conduct rules on.

Two hops, both over HTTP, never a direct read of another service's schema - tenant-
service owns the tenant's own entity_type, config-service owns what that entity type
means for credit eligibility. Degrades to "eligible" on either hop failing rather than
refusing every ingest during a transient outage - the same choice ``active_rules()``
makes when config-service is unreachable, for the same reason: an availability failure
in a dependency must not silently look identical to "this bank cannot lend."
"""
from __future__ import annotations

import logging

import httpx

from cp_common import settings

log = logging.getLogger("lane_c")


def can_extend_credit(tenant_id: str) -> tuple[bool, str]:
    """(eligible, reason). Fails open (eligible=True) on any lookup failure - see
    module docstring for why that direction is the safe one here."""
    try:
        r = httpx.get(f"{settings.tenant_service_url}/internal/{tenant_id}/identity",
                     headers={"x-internal-key": settings.internal_api_key}, timeout=5.0)
        r.raise_for_status()
        entity_type = r.json().get("entity_type", "")
    except Exception as exc:  # noqa: BLE001
        log.warning("tenant-service unreachable for %s: %s", tenant_id, exc)
        return True, "tenant identity unavailable; not refusing on an availability failure"

    if not entity_type:
        return True, "tenant has no entity_type on record"

    try:
        r = httpx.get(f"{settings.config_service_url}/internal/entity-types/{entity_type}",
                     headers={"x-internal-key": settings.internal_api_key}, timeout=5.0)
        r.raise_for_status()
        info = r.json()
    except Exception as exc:  # noqa: BLE001
        log.warning("config-service unreachable for entity type %s: %s", entity_type, exc)
        return True, "entity-type policy unavailable; not refusing on an availability failure"

    if info.get("can_extend_credit"):
        return True, ""
    return False, (f"entity type '{entity_type}' ({info.get('label', entity_type)}) "
                   f"cannot extend credit and has no borrowers for Lane C to monitor")
