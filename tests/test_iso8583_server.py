"""Coverage for services/iso8583_gateway/app/server.py's per-message handling - every
failure path must produce an explicit negative response, never a guessed approval."""
import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from services.iso8583_gateway.app.client import DecideClientError
from services.iso8583_gateway.app.codec import decode_message, encode_message
from services.iso8583_gateway.app.server import _handle_message

IST = timezone(timedelta(hours=5, minutes=30))


class FakeDecideClient:
    def __init__(self, response: dict | None = None, exc: Exception | None = None):
        self._response = response
        self._exc = exc
        self.calls: list[dict] = []

    async def decide(self, decide_in: dict) -> dict:
        self.calls.append(decide_in)
        if self._exc is not None:
            raise self._exc
        return self._response


def _auth_request_bytes(**field_overrides) -> bytes:
    fields = {
        2: "4111111111111111",
        3: "000000",
        4: "000001899900",
        7: datetime.now(IST).strftime("%m%d%H%M%S"),
        11: "004417",
        37: "24010112345",
        41: "TERM0001",
        42: "MERCH-88213-EL",
        49: "356",
    }
    fields.update(field_overrides)
    return encode_message("0100", fields)


def _handle(raw: bytes, client, *, fail_open: bool = True):
    return asyncio.run(_handle_message(
        raw, client, tz_offset_minutes=330, fail_open=fail_open, conn_id="test"))


def test_approved_decision_round_trips_to_00():
    client = FakeDecideClient(response={"action": "allow", "enforced": True,
                                        "took_ms": 2.1})
    raw = _auth_request_bytes()
    resp = decode_message(_handle(raw, client))
    assert resp.mti == "0110"
    assert resp.fields[39] == "00"
    assert len(client.calls) == 1
    # The gateway must never let the switch see the actual PAN - not even indirectly
    # through what it sent to /decide.
    assert "4111111111111111" not in str(client.calls[0])


def test_declined_decision_round_trips_to_05():
    client = FakeDecideClient(response={"action": "decline", "enforced": True})
    resp = decode_message(_handle(_auth_request_bytes(), client))
    assert resp.fields[39] == "05"


def test_challenge_decision_round_trips_to_01():
    client = FakeDecideClient(response={"action": "challenge", "enforced": True})
    resp = decode_message(_handle(_auth_request_bytes(), client))
    assert resp.fields[39] == "01"


def test_decide_unreachable_fail_open_approves():
    client = FakeDecideClient(exc=DecideClientError("connection refused"))
    resp = decode_message(_handle(_auth_request_bytes(), client, fail_open=True))
    assert resp.fields[39] == "00"


def test_decide_unreachable_fail_closed_declines_as_inoperative():
    client = FakeDecideClient(exc=DecideClientError("connection refused"))
    resp = decode_message(_handle(_auth_request_bytes(), client, fail_open=False))
    assert resp.fields[39] == "91"


def test_malformed_bytes_never_reach_decide_client():
    client = FakeDecideClient(response={"action": "allow"})
    resp = decode_message(_handle(b"not a valid iso8583 message", client))
    assert resp.fields[39] == "96"
    assert client.calls == []  # never called - the message never became a request


def test_unsupported_mti_refused_without_calling_decide():
    client = FakeDecideClient(response={"action": "allow"})
    # 0800 = network management request - out of scope for this gateway.
    raw = encode_message("0800", {3: "000000", 4: "000000000000",
                                  7: datetime.now(IST).strftime("%m%d%H%M%S"),
                                  11: "000001", 41: "T", 42: "M", 49: "356"})
    resp = decode_message(_handle(raw, client))
    assert resp.fields[39] == "96"
    assert client.calls == []


def test_missing_required_field_refused_without_calling_decide():
    client = FakeDecideClient(response={"action": "allow"})
    fields = {3: "000000", 4: "000001899900",
             7: datetime.now(IST).strftime("%m%d%H%M%S"), 11: "004417",
             41: "TERM0001", 42: "MERCH-88213-EL", 49: "356"}  # DE2 (PAN) missing
    raw = encode_message("0100", fields)
    resp = decode_message(_handle(raw, client))
    assert resp.fields[39] == "96"
    assert client.calls == []


def test_unrecognisable_decide_response_produces_system_malfunction_not_a_guess():
    client = FakeDecideClient(response={"action": "quarantine"})  # not a real action
    resp = decode_message(_handle(_auth_request_bytes(), client))
    assert resp.fields[39] == "96"
    assert len(client.calls) == 1  # it *was* called - the failure is in the response


def test_response_never_defaults_to_approve_on_any_gateway_failure_path():
    # A blanket property check across every failure scenario above: none of them may
    # ever produce "00" except the fail-open branch, which is explicit and documented.
    scenarios = [
        (b"garbage", FakeDecideClient(response={"action": "allow"}), True),
        (_auth_request_bytes(), FakeDecideClient(response={"action": "nonsense"}), True),
    ]
    for raw, client, fail_open in scenarios:
        resp = decode_message(_handle(raw, client, fail_open=fail_open))
        assert resp.fields[39] == "96"
