"""Calls into Fraud360 exactly the way an external Lane A JSON integrator would.

No shortcut, no internal-only path: this gateway authenticates with a machine credential
(§3.2 of the integration spec) and calls ``POST /decide`` (§4.2) over HTTP, the same
contract documented for every other Lane A integrator. That is a deliberate design
choice, not a placeholder - it is what keeps "two front doors, one brain" true at the
implementation level, not just as an architecture diagram: there is exactly one code
path in decision_service that ever produces a decision, and this gateway is a client of
it like any other.
"""
from __future__ import annotations

import logging
import time

import httpx

log = logging.getLogger("iso8583_gateway.client")


class DecideClientError(Exception):
    """/decide (or the token exchange in front of it) could not be reached or refused
    the call. Distinct from a normal decision response - the caller maps this to a
    failure response (mapper.failure_response), never to a guessed action."""


class DecideClient:
    """Thin async client: acquire/cache a bearer token, call /decide, refresh once on 401.

    One instance per gateway process (one tenant, one credential - see settings.py's
    iso8583_tenant_id/client_id). A deployment fronting more than one tenant over 8583
    would need per-connection credential resolution, which is out of scope for the MVP -
    see docs/ISO8583_LANE_A_SCOPING.md §3.
    """

    def __init__(self, *, tenant_service_url: str, decision_service_url: str,
                client_id: str, client_secret: str, tenant_id: str,
                timeout_seconds: float = 5.0):
        if not (client_id and client_secret and tenant_id):
            raise DecideClientError(
                "iso8583_client_id, iso8583_client_secret and iso8583_tenant_id must "
                "all be set - a gateway with no credential cannot call /decide, and "
                "starting anyway would mean discovering that on the first live message")
        self._tenant_service_url = tenant_service_url.rstrip("/")
        self._decision_service_url = decision_service_url.rstrip("/")
        self._client_id = client_id
        self._client_secret = client_secret
        self._tenant_id = tenant_id
        self._timeout = timeout_seconds
        self._token = ""
        self._token_expires_at = 0.0
        self._http = httpx.AsyncClient(timeout=timeout_seconds)

    async def aclose(self) -> None:
        await self._http.aclose()

    async def _ensure_token(self, *, force: bool = False) -> str:
        if not force and self._token and time.monotonic() < self._token_expires_at:
            return self._token
        try:
            r = await self._http.post(
                f"{self._tenant_service_url}/machine/token",
                json={"grant_type": "client_credentials", "client_id": self._client_id,
                     "client_secret": self._client_secret})
            r.raise_for_status()
        except httpx.HTTPError as exc:
            raise DecideClientError(f"token exchange failed: {exc}") from exc
        body = r.json()
        self._token = body["access_token"]
        # Refresh a little early so an in-flight authorisation never races an expiry.
        self._token_expires_at = time.monotonic() + max(body.get("expires_in", 0) - 30, 30)
        return self._token

    async def decide(self, decide_in: dict) -> dict:
        """Call POST /decide. Returns the DecideOut body. Raises DecideClientError on
        any failure - the caller decides fail-open/closed, this method never guesses."""
        payload = {k: v for k, v in decide_in.items() if not k.startswith("_")}
        token = await self._ensure_token()
        for attempt in (1, 2):
            try:
                r = await self._http.post(
                    f"{self._decision_service_url}/decide",
                    params={"tenant_id": self._tenant_id},
                    headers={"Authorization": f"Bearer {token}"},
                    json=payload)
            except httpx.HTTPError as exc:
                raise DecideClientError(f"/decide unreachable: {exc}") from exc
            if r.status_code == 401 and attempt == 1:
                token = await self._ensure_token(force=True)
                continue
            if r.status_code >= 400:
                raise DecideClientError(
                    f"/decide refused the request: {r.status_code} {r.text[:300]}")
            return r.json()
        raise DecideClientError("/decide still refusing after a token refresh")
