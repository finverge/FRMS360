"""Calls the DPDP Compliance Platform's Data Minimization Engine
(POST /masking/apply — Finverge_DPDP_FSD_v1.0.docx Sec. 4.2) for the
export/drill masking path — this service's first real external consumer of
that platform (Finverge_DPDP_HLD_v1.0.docx Sec. 7.2).

Deliberately narrow blast radius: only ``export_rows`` and ``drill`` in
routes/dashboards.py call through this client. Every other dashboard's
``privacy.mask_rows(..., False)`` call, ``mask_value``'s use inside
``graph()`` (which depends on it being a fast, pure, local function for its
per-account pseudonym-collision handling), and ``mask_payload`` (the
evidence drawer's nested-structure masking) are untouched — swapping those
too was not this integration's scope and each has its own reason not to
take on network latency here.

Fails closed to this service's own local masking (privacy.py), never to
unmasked data: any failure reaching the platform (network error, timeout,
non-2xx, no policy configured for this tenant/purpose) falls back to
exactly the masking privacy.py already did before this integration
existed — masking is never skipped, only its authority source changes,
and only on the happy path.
"""
from __future__ import annotations

import logging
import os

import httpx
from fastapi.encoders import jsonable_encoder

from . import privacy

log = logging.getLogger("analytics_service.dpdp_client")

DPDP_PLATFORM_URL = os.environ.get("DPDP_PLATFORM_URL", "http://localhost:8110")
# One shared purpose for every export/drill masking call — Fraud360's own
# field-masking rules don't vary by dashboard or entity (Fraud360's
# PII_FIELDS/mask_value already apply the same rule set everywhere), so a
# single policy per tenant is the honest mapping, not an artificial split.
DPDP_MASKING_PURPOSE = "fraud360_analytics_export"


class DpdpMaskingClientError(Exception):
    """/masking/apply could not be reached or refused the call. Callers
    catch this and fall back to local masking — they never guess, and
    never treat this as "so return the data unmasked"."""


class DpdpMaskingClient:
    def __init__(self, *, base_url: str = DPDP_PLATFORM_URL, timeout_seconds: float = 2.0):
        self._base_url = base_url.rstrip("/")
        self._http = httpx.Client(timeout=timeout_seconds)

    def close(self) -> None:
        self._http.close()

    def mask_rows(self, tenant_id: str, rows: list[dict], actor: str) -> list[dict]:
        """POSTs the batch once per call (not once per field/row) — the
        whole reason this is a real HTTP integration rather than a
        per-value RPC. Raises DpdpMaskingClientError on any failure."""
        if not rows:
            return rows
        try:
            # Raw DB rows carry datetime/Decimal/etc. that httpx's own
            # (strict) json encoder can't serialize — jsonable_encoder is
            # the same conversion FastAPI already applies when these rows
            # go out as a normal response elsewhere in this service, so
            # the wire format this platform sees matches what every other
            # consumer of these rows already gets.
            r = self._http.post(f"{self._base_url}/masking/apply", json=jsonable_encoder({
                "tenant_id": tenant_id, "purpose": DPDP_MASKING_PURPOSE, "actor": actor, "records": rows,
            }))
        except httpx.HTTPError as exc:
            raise DpdpMaskingClientError(f"/masking/apply unreachable: {exc}") from exc
        if r.status_code >= 400:
            raise DpdpMaskingClientError(f"/masking/apply refused the request: {r.status_code} {r.text[:300]}")
        return r.json()["records"]


_client = DpdpMaskingClient()


def mask_rows_via_dpdp(tenant_id: str, rows: list[dict], reveal: bool, actor: str) -> list[dict]:
    """Drop-in replacement for ``privacy.mask_rows(rows, reveal)`` at the
    export/drill call sites only. ``reveal=True`` still means "return
    unmasked" exactly as before — the RBAC/justification gate in
    ``_resolve_reveal`` (dashboards.py), which decided that boolean, is
    completely untouched by this function; this only changes who performs
    the masking when ``reveal`` is False."""
    if reveal:
        return rows
    try:
        return _client.mask_rows(tenant_id, rows, actor)
    except DpdpMaskingClientError as exc:
        log.warning("DPDP Compliance Platform unavailable for tenant %s, falling back to local masking: %s",
                    tenant_id, exc)
        return privacy.mask_rows(rows, False)
