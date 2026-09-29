"""ISO 8583 message encode/decode and TCP framing.

Wire format (the MVP default - see docs/ISO8583_LANE_A_SCOPING.md §7 on why this is a
default, not a confirmed vendor spec):

* 2-byte big-endian length header, counting the bytes that follow (MTI + bitmap + fields),
  not including the header itself. This is the most common convention on TCP/IP switch
  links; a 4-byte header or no header at all (message-boundary-per-connection) are the
  documented alternatives to add if a pilot vendor needs them.
* MTI: 4 ASCII digits.
* Primary bitmap: 16 ASCII hex characters (64 bits, DE1-DE64). Bit 1 (DE1) would signal a
  secondary bitmap for DE65-128; this gateway does not need any field above DE49, so a
  secondary bitmap is refused rather than silently mishandled - see ``decode_message``.
* Each present field encoded per its ``FieldDef`` - fixed-width zero/space-padded, or an
  LLVAR/LLLVAR 2- or 3-digit ASCII length prefix followed by that many characters.
"""
from __future__ import annotations

import asyncio
import struct
from dataclasses import dataclass, field

from .spec import FIXED, LLLVAR, LLVAR, STANDARD_FIELDS, Iso8583SpecError

LENGTH_HEADER_BYTES = 2
MAX_MESSAGE_BYTES = 8192  # generous for a 8583 auth message; guards against a garbage stream


@dataclass
class Iso8583Message:
    mti: str
    fields: dict[int, str] = field(default_factory=dict)

    def get(self, de: int, default: str = "") -> str:
        return self.fields.get(de, default)


# --------------------------------------------------------------------------- bitmap

def _bitmap_to_hex(des_present: set[int]) -> str:
    if any(d < 1 or d > 64 for d in des_present):
        raise Iso8583SpecError("only DE1-DE64 are supported; no secondary bitmap")
    bits = 0
    for de in des_present:
        bits |= 1 << (64 - de)
    return f"{bits:016X}"


def _hex_to_des_present(hexstr: str) -> set[int]:
    if len(hexstr) != 16:
        raise Iso8583SpecError(f"primary bitmap must be 16 hex chars, got {len(hexstr)}")
    try:
        bits = int(hexstr, 16)
    except ValueError as exc:
        raise Iso8583SpecError(f"primary bitmap is not valid hex: {hexstr!r}") from exc
    if bits & (1 << 63):  # bit 1 (DE1) = secondary bitmap present
        raise Iso8583SpecError(
            "message declares a secondary bitmap (DE65-128); this gateway only maps "
            "DE1-DE64 and refuses rather than silently drop fields it cannot represent")
    return {de for de in range(1, 65) if bits & (1 << (64 - de))}


# --------------------------------------------------------------------------- fields

def _pack_field(fd, value: str) -> str:
    if fd.kind == FIXED:
        if len(value) > fd.length:
            raise Iso8583SpecError(f"DE{fd.de} ({fd.name}) value {value!r} exceeds fixed "
                                   f"width {fd.length}")
        pad = "0" if fd.fmt == "n" else " "
        return value.rjust(fd.length, pad) if fd.fmt == "n" else value.ljust(fd.length, pad)
    if fd.kind == LLVAR:
        if len(value) > fd.length:
            raise Iso8583SpecError(f"DE{fd.de} ({fd.name}) value exceeds LLVAR max "
                                   f"{fd.length}")
        return f"{len(value):02d}{value}"
    if fd.kind == LLLVAR:
        if len(value) > fd.length:
            raise Iso8583SpecError(f"DE{fd.de} ({fd.name}) value exceeds LLLVAR max "
                                   f"{fd.length}")
        return f"{len(value):03d}{value}"
    raise Iso8583SpecError(f"unknown field kind {fd.kind!r}")


def _unpack_field(fd, body: str, pos: int) -> tuple[str, int]:
    if fd.kind == FIXED:
        raw = body[pos:pos + fd.length]
        if len(raw) != fd.length:
            raise Iso8583SpecError(f"DE{fd.de} ({fd.name}) truncated: expected "
                                   f"{fd.length} chars, {len(raw)} remain")
        # Numeric fixed fields (DE3 processing code, DE7 date/time, DE11 STAN, DE49
        # currency) are fixed-format codes, not quantities - leading zeros are part of
        # their meaning and must round-trip exactly. Only DE4 (amount) is ever treated
        # as an integer, and that conversion happens in mapper.py, not here: int("007")
        # parses correctly without pre-stripping, so there is nothing to gain from
        # stripping here and real information to lose for every other numeric field.
        value = raw if fd.fmt == "n" else raw.rstrip()
        return value, pos + fd.length
    if fd.kind in (LLVAR, LLLVAR):
        n = 2 if fd.kind == LLVAR else 3
        len_str = body[pos:pos + n]
        if len(len_str) != n or not len_str.isdigit():
            raise Iso8583SpecError(f"DE{fd.de} ({fd.name}) has a malformed length prefix")
        flen = int(len_str)
        if flen > fd.length:
            raise Iso8583SpecError(f"DE{fd.de} ({fd.name}) declares length {flen}, max "
                                   f"is {fd.length}")
        start = pos + n
        raw = body[start:start + flen]
        if len(raw) != flen:
            raise Iso8583SpecError(f"DE{fd.de} ({fd.name}) truncated: declared {flen}, "
                                   f"{len(raw)} remain")
        return raw, start + flen
    raise Iso8583SpecError(f"unknown field kind {fd.kind!r}")


# --------------------------------------------------------------------------- message

def encode_message(mti: str, fields: dict[int, str]) -> bytes:
    """Canonical field dict -> wire bytes (MTI + bitmap + fields), no length header."""
    if not (len(mti) == 4 and mti.isdigit()):
        raise Iso8583SpecError(f"MTI must be 4 digits, got {mti!r}")
    unknown = [de for de in fields if de not in STANDARD_FIELDS]
    if unknown:
        raise Iso8583SpecError(
            f"DE{unknown} not in STANDARD_FIELDS - private/vendor fields need an "
            f"explicit extension, not silent passthrough (see spec.py docstring)")
    des_present = set(fields.keys())
    body = mti + _bitmap_to_hex(des_present)
    for de in sorted(des_present):
        body += _pack_field(STANDARD_FIELDS[de], fields[de])
    return body.encode("ascii")


def decode_message(payload: bytes) -> Iso8583Message:
    """Wire bytes (no length header) -> ``Iso8583Message``."""
    try:
        text = payload.decode("ascii")
    except UnicodeDecodeError as exc:
        raise Iso8583SpecError("message is not ASCII - BCD/EBCDIC framing is not "
                               "supported by this gateway") from exc
    if len(text) < 20:
        raise Iso8583SpecError("message shorter than MTI+bitmap (20 chars)")
    mti, bitmap_hex, body = text[:4], text[4:20], text[20:]
    if not mti.isdigit():
        raise Iso8583SpecError(f"MTI must be 4 digits, got {mti!r}")
    des_present = _hex_to_des_present(bitmap_hex)
    fields: dict[int, str] = {}
    pos = 0
    for de in sorted(des_present):
        fd = STANDARD_FIELDS.get(de)
        if fd is None:
            raise Iso8583SpecError(
                f"message sets DE{de}, which is outside STANDARD_FIELDS - this gateway "
                f"cannot interpret it and refuses rather than drop it silently")
        value, pos = _unpack_field(fd, body, pos)
        fields[de] = value
    return Iso8583Message(mti=mti, fields=fields)


# --------------------------------------------------------------------------- framing

def frame(payload: bytes) -> bytes:
    if len(payload) > MAX_MESSAGE_BYTES:
        raise Iso8583SpecError(f"message is {len(payload)} bytes; refusing to frame "
                               f"more than {MAX_MESSAGE_BYTES}")
    return struct.pack(">H", len(payload)) + payload


async def read_framed(reader: asyncio.StreamReader) -> bytes:
    """Read one length-prefixed message. Raises ``asyncio.IncompleteReadError`` at EOF."""
    header = await reader.readexactly(LENGTH_HEADER_BYTES)
    (length,) = struct.unpack(">H", header)
    if length == 0 or length > MAX_MESSAGE_BYTES:
        raise Iso8583SpecError(f"declared message length {length} is out of bounds "
                               f"(1-{MAX_MESSAGE_BYTES})")
    return await reader.readexactly(length)
