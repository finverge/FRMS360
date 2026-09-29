"""Round-trip and error-path coverage for services/iso8583_gateway/app/codec.py.

No database needed - this module is pure message parsing, but it still runs inside the
full suite (conftest.py's ``database`` fixture is session-scoped autouse, so these tests
pay that setup cost like every other test file without needing it themselves).
"""
import pytest

from services.iso8583_gateway.app.codec import (
    Iso8583SpecError, decode_message, encode_message, frame,
)


def _sample_fields() -> dict:
    return {
        2: "4111111111111111",
        3: "000000",
        4: "000001899900",
        7: "0817143210",
        11: "004417",
        37: "24010112345",
        41: "TERM0001",
        42: "MERCH-88213-EL",
        49: "356",
    }


def test_round_trip_preserves_every_field():
    fields = _sample_fields()
    wire = encode_message("0100", fields)
    msg = decode_message(wire)
    assert msg.mti == "0100"
    assert msg.fields == fields


def test_llvar_field_round_trips_exact_length():
    # DE2 (PAN) is LLVAR - a 13-digit and a 19-digit PAN must both survive.
    for pan in ("4111111111111", "4111111111111111111"):
        fields = _sample_fields()
        fields[2] = pan
        wire = encode_message("0100", fields)
        msg = decode_message(wire)
        assert msg.fields[2] == pan


def test_fixed_numeric_field_preserves_leading_zeros():
    # DE3 (processing code) "000000" must not come back as "0" - it is a code, not a
    # quantity. This is the exact bug class the codec module's docstring calls out.
    fields = _sample_fields()
    fields[3] = "003000"
    wire = encode_message("0100", fields)
    msg = decode_message(wire)
    assert msg.fields[3] == "003000"


def test_de7_transmission_time_round_trips_exact_digits():
    fields = _sample_fields()
    fields[7] = "0101000005"  # New Year's Day, five seconds past midnight
    wire = encode_message("0100", fields)
    msg = decode_message(wire)
    assert msg.fields[7] == "0101000005"


def test_alphanumeric_fixed_field_strips_only_padding():
    fields = _sample_fields()
    fields[41] = "T1"  # shorter than the 8-char fixed width
    wire = encode_message("0100", fields)
    msg = decode_message(wire)
    assert msg.fields[41] == "T1"


def test_only_present_fields_are_encoded():
    minimal = {3: "000000", 4: "000000010000", 7: "0817143210", 11: "000001",
              41: "T", 42: "M", 49: "356"}
    wire = encode_message("0100", minimal)
    msg = decode_message(wire)
    assert set(msg.fields) == set(minimal)
    assert 2 not in msg.fields  # PAN was never set


def test_unknown_field_refused_at_encode_not_silently_dropped():
    fields = _sample_fields()
    fields[62] = "some-vendor-private-field"
    with pytest.raises(Iso8583SpecError, match=r"DE\[62\]"):
        encode_message("0100", fields)


def test_secondary_bitmap_refused_at_decode():
    # Bit 1 set (secondary bitmap present) with no fields beyond DE1 itself.
    wire = ("0100" + "8" + "0" * 15).encode("ascii")
    with pytest.raises(Iso8583SpecError, match="secondary bitmap"):
        decode_message(wire)


def test_field_outside_standard_fields_refused_at_decode():
    # Craft a bitmap that claims DE64 is present - not in STANDARD_FIELDS.
    bitmap = f"{1:016X}"  # bit for DE64 = lowest bit
    wire = ("0100" + bitmap).encode("ascii")
    with pytest.raises(Iso8583SpecError, match="DE64"):
        decode_message(wire)


def test_truncated_message_refused():
    fields = _sample_fields()
    wire = encode_message("0100", fields)
    with pytest.raises(Iso8583SpecError):
        decode_message(wire[:-5])


def test_llvar_value_exceeding_max_length_refused():
    fields = _sample_fields()
    fields[2] = "1" * 20  # DE2 max is 19
    with pytest.raises(Iso8583SpecError, match="LLVAR max"):
        encode_message("0100", fields)


def test_non_ascii_payload_refused_at_decode():
    with pytest.raises(Iso8583SpecError, match="ASCII"):
        decode_message(b"\xff\xfe\x00\x01" + b"0" * 20)


def test_frame_prefixes_two_byte_big_endian_length():
    payload = b"hello world"
    framed = frame(payload)
    assert framed[:2] == (11).to_bytes(2, "big")
    assert framed[2:] == payload


def test_read_framed_round_trips_over_a_stream_pair():
    # No pytest-asyncio dependency in this project (pytest.ini has no such plugin) -
    # driven directly through asyncio.run() instead of an async test function.
    import asyncio

    from services.iso8583_gateway.app.codec import read_framed

    payload = encode_message("0100", _sample_fields())

    async def _run():
        async def _server(reader, writer):
            got = await read_framed(reader)
            assert got == payload
            writer.close()
            await writer.wait_closed()
            server.close()

        server = await asyncio.start_server(_server, "127.0.0.1", 0)
        addr = server.sockets[0].getsockname()
        async with server:
            _reader, writer = await asyncio.open_connection(addr[0], addr[1])
            writer.write(frame(payload))
            await writer.drain()
            await asyncio.wait_for(server.wait_closed(), timeout=5)
            writer.close()

    asyncio.run(_run())
