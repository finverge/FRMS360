"""Synthetic ISO 8583 traffic through the real pipeline: codec -> mapper -> the actual
decision-service HTTP route -> the actual rule engine -> the actual ISO 8583 response.

Nothing here is mocked below the credential exchange. ``_LocalDecideClient`` skips
token acquisition (that flow has its own dedicated coverage in test_machine_credentials.py
and is orthogonal to "does the rule engine correctly score a card authorisation") and
calls decision-service's real FastAPI app in-process over ASGI - same route function, same
auth dependency, same policy/catalogue lookup, same evaluator. A human bearer token is
used because ``require_machine_scope`` admits a human unchanged (cp_common.auth); the
credential *class* used to authenticate is not what this file is testing.
"""
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from sqlalchemy import text

from cp_common.db import SessionLocal
from services.decision_service.app import store as decision_store
from services.decision_service.app.counters import PostgresCounterStore
from services.iso8583_gateway.app import mapper as iso_mapper
from services.iso8583_gateway.app.codec import decode_message, encode_message
from services.iso8583_gateway.app.server import _handle_message

IST = timezone(timedelta(hours=5, minutes=30))

#: A policy this file owns for the duration of its tests - CARD, enforced (shadow off),
#: so a match actually declines rather than only recording what it would have done. Set
#: via store.set_policy, the documented test/break-glass override (decision_service/app/
#: store.py) - never through config-service, so no other test's policy is disturbed.
_CARD_POLICY = {
    "version": "iso8583-e2e-1.0.0",
    "rails": {"CARD": {"mode": "inline", "budget_ms": 100, "fail": "open",
                       "actions": ["challenge", "decline"], "shadow": False,
                       "decline_from_severity": "critical"}},
}


class _LocalDecideClient:
    """Same ``async def decide(self, decide_in) -> dict`` interface as the production
    ``iso8583_gateway.app.client.DecideClient``, backed by the real decision-service ASGI
    app instead of a real socket - see the module docstring for why the token exchange is
    swapped out rather than the route itself."""

    def __init__(self, *, bearer_token: str, tenant_id: str):
        from services.decision_service.app.main import app as decision_app

        self._tenant_id = tenant_id
        self._token = bearer_token
        self._http = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=decision_app),
            base_url="http://decision-service.test")

    async def decide(self, decide_in: dict) -> dict:
        payload = {k: v for k, v in decide_in.items() if not k.startswith("_")}
        r = await self._http.post(
            "/decide", params={"tenant_id": self._tenant_id},
            headers={"Authorization": f"Bearer {self._token}"}, json=payload)
        if r.status_code >= 400:
            from services.iso8583_gateway.app.client import DecideClientError
            raise DecideClientError(f"/decide refused: {r.status_code} {r.text[:300]}")
        return r.json()

    async def aclose(self) -> None:
        await self._http.aclose()


@pytest.fixture
def decide_client(tid, token_for, monkeypatch):
    decision_store.refresh(tid)  # force-load the real catalogue synchronously - see
                                 # store.catalogue_for's docstring on why the lazy path
                                 # (background thread) would otherwise race this test
    decision_store.set_policy(tid, _CARD_POLICY)
    # Severity is scored across every family that fired on the payment (cp_common.
    # scoring) - one velocity hit alone (weight 100) sits well under the platform
    # default critical band (380) by design: "one velocity hit is not critical;
    # velocity with layering and structuring is" (decide.severity_of's docstring).
    # These tests are about proving the ISO 8583 pipeline reaches the real evaluator
    # and reflects its verdict correctly, not about assembling a multi-family fraud
    # ring from one authorisation message - so the tenant's own severity bands
    # (frm_policy, fetched independently of decision_policy) are lowered here, the
    # same way a bank's own board-approved bands would differ from the platform
    # default. decision.py imports the name directly, so the route module - not
    # store.py - is what has to be patched.
    from services.decision_service.app.routes import decide as decide_route
    monkeypatch.setattr(decide_route, "frm_policy_for",
                        lambda tenant_id: {"severity_critical_score": 50,
                                          "severity_high_score": 30,
                                          "severity_medium_score": 10})
    token = token_for("tenant_admin")["Authorization"].removeprefix("Bearer ")
    client = _LocalDecideClient(bearer_token=token, tenant_id=tid)
    yield client
    decision_store.clear_override(tid)


def _card_auth_bytes(pan: str, amount_paise: int, *, merchant="MERCH-88213-EL",
                     terminal="TERM0001", stan="004417", rrn="24010112345") -> bytes:
    fields = {
        2: pan,
        3: "000000",
        4: f"{amount_paise:012d}",
        7: datetime.now(IST).strftime("%m%d%H%M%S"),
        11: stan,
        37: rrn,
        41: terminal,
        42: merchant,
        49: "356",
    }
    return encode_message("0100", fields)


def _seed_baseline(tenant_id: str, account: str, baseline_mean_paise: float) -> None:
    """Seed the account's 30-day mean transaction amount - the raw counter VEL-01 is
    actually computed from (cp_common.observations.observe: ``amount / ctx.
    baseline_mean_paise``). ``value_vs_baseline`` is the catalogue's name for the
    *derived* ratio, not a stored counter in its own right - it is worked out fresh from
    this and the payment's own amount every time, on both lanes, via the one shared
    ``observe()`` function (see evaluate.py's docstring on why that indirection exists)."""
    db = SessionLocal()
    try:
        PostgresCounterStore(db).write(tenant_id, account, {
            "baseline_mean_paise": baseline_mean_paise})
        db.commit()
    finally:
        db.close()


def _clear_baseline(tenant_id: str, account: str) -> None:
    db = SessionLocal()
    try:
        db.execute(text(
            "DELETE FROM decision.account_counters "
            "WHERE tenant_id = :t AND account = :a"), {"t": tenant_id, "a": account})
        db.commit()
    finally:
        db.close()


# ------------------------------------------------------------------ clean traffic
def test_a_clean_card_authorisation_is_approved_end_to_end(decide_client):
    pan = "4111111111119901"
    raw = _card_auth_bytes(pan, amount_paise=250000)  # a modest, unremarkable amount
    account = iso_mapper.mask_pan(pan)
    _clear_baseline(decide_client._tenant_id, account)  # ensure no stray prior state
    try:
        response_bytes = _run(raw, decide_client)
        resp = decode_message(response_bytes)
        assert resp.mti == "0110"
        assert resp.fields[39] == "00", f"clean traffic was not approved: {resp.fields}"
        assert resp.fields[11] == "004417"  # STAN echoed
        assert resp.fields[37] == "24010112345"  # RRN echoed
    finally:
        _clear_baseline(decide_client._tenant_id, account)


# ------------------------------------------------------------------ synthetic fraud
def test_a_velocity_spike_synthesised_via_iso8583_is_declined_end_to_end(decide_client):
    """The same VEL-01 pattern the HLD's worked example shows over JSON (value 5x the
    30-day baseline), synthesised here as a raw ISO 8583 authorisation instead - proving
    the gateway's decode/map path reaches the identical rule engine outcome."""
    pan = "4111111111119902"
    account = iso_mapper.mask_pan(pan)
    amount_paise = 450_000_000       # a large card authorisation, ~Rs 45,00,000
    baseline_mean_paise = 1_000_000  # this account's usual ticket size, ~Rs 10,000
    # ratio = 450x - VEL-01's threshold is 5x
    _seed_baseline(decide_client._tenant_id, account, baseline_mean_paise)
    try:
        raw = _card_auth_bytes(pan, amount_paise=amount_paise)
        response_bytes = _run(raw, decide_client)
        resp = decode_message(response_bytes)
        assert resp.fields[39] == "05", (
            f"a synthetic 450x-baseline spike did not decline; DE39={resp.fields[39]}")
    finally:
        _clear_baseline(decide_client._tenant_id, account)


def test_two_synthetic_pans_are_scored_independently(decide_client):
    """One account's seeded baseline must never leak onto another's decision - basic
    account isolation, proven with synthetic traffic rather than asserted from reading
    the code."""
    hot_pan, clean_pan = "4111111111119903", "4111111111119904"
    hot_account = iso_mapper.mask_pan(hot_pan)
    clean_account = iso_mapper.mask_pan(clean_pan)
    _seed_baseline(decide_client._tenant_id, hot_account, baseline_mean_paise=1_000_000)
    _clear_baseline(decide_client._tenant_id, clean_account)
    try:
        hot_resp = decode_message(_run(_card_auth_bytes(hot_pan, 450000000), decide_client))
        clean_resp = decode_message(_run(_card_auth_bytes(clean_pan, 250000), decide_client))
        assert hot_resp.fields[39] == "05"
        assert clean_resp.fields[39] == "00"
    finally:
        _clear_baseline(decide_client._tenant_id, hot_account)
        _clear_baseline(decide_client._tenant_id, clean_account)


def _run(raw: bytes, client: _LocalDecideClient) -> bytes:
    import asyncio
    return asyncio.run(_handle_message(
        raw, client, tz_offset_minutes=330, fail_open=True, conn_id="e2e-test"))

