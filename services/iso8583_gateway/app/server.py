"""The asyncio TCP listener - one persistent connection per switch, many messages each.

Per-message flow: read a framed message -> decode -> map to a /decide request -> call
/decide -> map the response back -> encode -> frame -> write. Any failure at any stage
produces an explicit negative response (mapper.failure_response); this gateway never lets
a parse error, a mapping error, or an unreachable /decide read back to the switch as an
approval it did not actually decide.
"""
from __future__ import annotations

import asyncio
import logging
import uuid

from .client import DecideClient, DecideClientError
from .codec import Iso8583SpecError, decode_message, encode_message, frame, read_framed
from .mapper import (
    MappingError, decide_out_to_iso_response, failure_response, iso_request_to_decide_in,
    malfunction_response,
)
from .spec import MTI_AUTH_REQUEST, MTI_AUTH_REQUEST_ADVICE, REQUEST_TO_RESPONSE_MTI

log = logging.getLogger("iso8583_gateway.server")

#: Counters a health/metrics endpoint can read - mirrors decision_service's
#: WRITE_FAILURES pattern (routes/decide.py) of a plain dict rather than a new
#: dependency, since this is the same "surface it, don't just log it" discipline.
STATS = {"connections": 0, "messages": 0, "approved": 0, "declined": 0, "challenged": 0,
        "gateway_failures": 0}


async def _handle_message(raw: bytes, client: DecideClient, *, tz_offset_minutes: int,
                          fail_open: bool, conn_id: str) -> bytes:
    STATS["messages"] += 1
    try:
        msg = decode_message(raw)
    except Iso8583SpecError as exc:
        log.warning("conn=%s malformed message, refusing: %s", conn_id, exc)
        STATS["gateway_failures"] += 1
        return encode_message(*_as_tuple(failure_response(MTI_AUTH_REQUEST, None,
                                                          fail_open=fail_open)))

    if msg.mti not in REQUEST_TO_RESPONSE_MTI:
        log.warning("conn=%s unsupported MTI %s - this gateway only handles "
                    "authorisation requests (0100/0120)", conn_id, msg.mti)
        STATS["gateway_failures"] += 1
        return encode_message(*_as_tuple(failure_response(MTI_AUTH_REQUEST, None,
                                                          fail_open=fail_open)))

    try:
        decide_in = iso_request_to_decide_in(msg, tz_offset_minutes=tz_offset_minutes)
    except MappingError as exc:
        log.warning("conn=%s could not map message to a decision request: %s", conn_id,
                   exc)
        STATS["gateway_failures"] += 1
        return encode_message(*_as_tuple(failure_response(msg.mti, None,
                                                          fail_open=fail_open)))

    try:
        decide_out = await client.decide(decide_in)
    except DecideClientError as exc:
        log.error("conn=%s /decide call failed, applying fail_open=%s: %s", conn_id,
                  fail_open, exc)
        STATS["gateway_failures"] += 1
        return encode_message(*_as_tuple(failure_response(msg.mti, decide_in,
                                                          fail_open=fail_open)))

    try:
        response_msg = decide_out_to_iso_response(msg.mti, decide_in, decide_out)
    except MappingError as exc:
        # Fraud360 DID answer - this is a gap in this gateway's own translation, not an
        # unreachable-service condition, so it is never gated by fail_open (see
        # mapper.malfunction_response's docstring).
        log.error("conn=%s got a /decide response this gateway cannot represent: %s. "
                  "This is a defect to fix, not a payment to guess on.", conn_id, exc)
        STATS["gateway_failures"] += 1
        return encode_message(*_as_tuple(malfunction_response(msg.mti, decide_in)))

    action = decide_out.get("action", "")
    STATS[{"allow": "approved", "decline": "declined",
          "challenge": "challenged"}.get(action, "gateway_failures")] += 1
    log.info("conn=%s txn_ref=%s action=%s enforced=%s took_ms=%s", conn_id,
             decide_in["txn_ref"], action, decide_out.get("enforced"),
             decide_out.get("took_ms"))
    return encode_message(*_as_tuple(response_msg))


def _as_tuple(msg):
    return msg.mti, msg.fields


def make_connection_handler(client: DecideClient, *, tz_offset_minutes: int,
                            fail_open: bool):
    """Returns an asyncio.start_server callback bound to one DecideClient."""

    async def handle_connection(reader: asyncio.StreamReader,
                                writer: asyncio.StreamWriter) -> None:
        peer = writer.get_extra_info("peername")
        conn_id = uuid.uuid4().hex[:8]
        STATS["connections"] += 1
        log.info("conn=%s connected from %s", conn_id, peer)
        try:
            while True:
                try:
                    raw = await read_framed(reader)
                except asyncio.IncompleteReadError:
                    break  # peer closed the connection - normal
                except Iso8583SpecError as exc:
                    log.warning("conn=%s bad framing, closing connection: %s", conn_id,
                               exc)
                    break

                try:
                    response_bytes = await _handle_message(
                        raw, client, tz_offset_minutes=tz_offset_minutes,
                        fail_open=fail_open, conn_id=conn_id)
                except Exception:  # noqa: BLE001
                    # A bug in this gateway must not take the socket down mid-session -
                    # a switch that loses its connection stops sending altogether, which
                    # is worse than one slow/failed message. Log the full trace; the
                    # switch already got an explicit failure response for this message
                    # inside _handle_message's own error handling above.
                    log.exception("conn=%s unhandled error processing a message", conn_id)
                    continue

                writer.write(frame(response_bytes))
                await writer.drain()
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:  # noqa: BLE001
                pass
            log.info("conn=%s disconnected", conn_id)

    return handle_connection


async def serve(*, host: str, port: int, client: DecideClient, tz_offset_minutes: int,
                fail_open: bool) -> asyncio.AbstractServer:
    handler = make_connection_handler(client, tz_offset_minutes=tz_offset_minutes,
                                      fail_open=fail_open)
    server = await asyncio.start_server(handler, host, port)
    log.info("iso8583_gateway listening on %s:%d", host, port)
    return server
