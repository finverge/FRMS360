"""ISO 8583 fields <-> Fraud360's canonical decision request/response.

This gateway is a protocol adapter in front of the same ``/decide`` contract every JSON
Lane A integrator uses (see docs/Fraud360-Integration-Handshaking-Spec §4.2) - it maps an
0100/0120 authorisation request into a ``DecideIn``-shaped dict, calls ``/decide`` exactly
as a JSON caller would, and maps the ``DecideOut`` response back into an 0110/0130. It
does not re-implement or shortcut any decision logic.
"""
from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone

from .codec import Iso8583Message
from .spec import REQUIRED_REQUEST_FIELDS, Iso8583SpecError

#: What the rail can execute is a property of the payment scheme (policy.py's
#: RAIL_ACTIONS), not of this gateway. This gateway exists for card-present/card-not-
#: present authorisation switches, so it always identifies itself to /decide as CARD -
#: see docs/ISO8583_LANE_A_SCOPING.md. A deployment fronting a different rail over 8583
#: would set this at configuration time, not have it inferred per-message.
RAIL = "CARD"

#: DE39 response codes. "00"/"05" are universally understood; "01" (refer to card
#: issuer) stands in for Fraud360's "challenge" outcome, which raw ISO 8583 authorisation
#: has no native equivalent for - a referral is the closest analogous outcome a card
#: switch already knows how to route. "96" and "91" are used for this gateway's own
#: failures, never for a real decision - see docs/ISO8583_LANE_A_SCOPING.md §7 that exact
#: code tables are network-specific and this mapping is a documented default, not a
#: confirmed dialect.
RESPONSE_CODE_FROM_ACTION = {"allow": "00", "decline": "05", "challenge": "01"}
RESPONSE_CODE_SYSTEM_MALFUNCTION = "96"   # malformed message / mapping failure
RESPONSE_CODE_ISSUER_INOPERATIVE = "91"   # /decide unreachable and this rail fails closed


class MappingError(ValueError):
    """A message cannot be turned into a decision request. Never silently defaulted."""


def mask_pan(pan: str) -> str:
    """Never let a raw PAN reach a log line, a decision payload, or storage.

    The canonical JSON contract already refuses raw PANs (Appendix A.3 of the
    integration spec: "Send a tokenised PAN reference, never a raw card number") - this
    gateway holds itself to the same rule at the point the PAN first arrives, since it is
    the one component that necessarily sees the wire-format PAN. See
    docs/ISO8583_LANE_A_SCOPING.md §5: whether this construction is sufficient for the
    bank's PCI scope is a compliance decision, not an engineering one, and is called out
    there as a blocking open question - this is the safe-by-default behaviour pending
    that sign-off, not a substitute for it.
    """
    digest = hashlib.sha256(pan.encode("ascii")).hexdigest()[:16]
    last4 = pan[-4:] if len(pan) >= 4 else pan
    return f"PANHASH-{digest}-{last4}"


def _resolve_transmission_ts(mmddhhmmss: str, tz_offset_minutes: int) -> datetime:
    """DE7 carries month/day/hour/min/sec but never a year - a real protocol limitation,
    not an oversight of this module. The year is inferred from the current date in the
    gateway's configured local offset, rolling back a year if that reading would
    otherwise land more than a day in the future (handles messages that arrive in the
    last hours of December for a transaction dated into January, and vice versa).

    ``tz_offset_minutes`` must be configured explicitly (no default baked in here) -
    DE7 never carries an offset, so treating it as any particular timezone is this
    gateway's own deliberate, documented choice, not something the protocol says. This
    is different from ``adapters.to_utc`` refusing naive timestamps outright: there, a
    JSON caller could have sent an offset and didn't; here, the wire format structurally
    cannot carry one, and refusing would mean refusing every ISO 8583 message.
    """
    if len(mmddhhmmss) != 10 or not mmddhhmmss.isdigit():
        raise MappingError(f"DE7 is not MMDDhhmmss: {mmddhhmmss!r}")
    mm, dd, hh, mi, ss = (int(mmddhhmmss[0:2]), int(mmddhhmmss[2:4]), int(mmddhhmmss[4:6]),
                          int(mmddhhmmss[6:8]), int(mmddhhmmss[8:10]))
    tz = timezone(timedelta(minutes=tz_offset_minutes))
    now_local = datetime.now(tz)
    try:
        candidate = datetime(now_local.year, mm, dd, hh, mi, ss, tzinfo=tz)
    except ValueError as exc:
        raise MappingError(f"DE7 is not a valid date/time: {mmddhhmmss!r}") from exc
    if candidate - now_local > timedelta(days=1):
        candidate = candidate.replace(year=now_local.year - 1)
    return candidate.astimezone(timezone.utc)


def iso_request_to_decide_in(msg: Iso8583Message, *, tz_offset_minutes: int) -> dict:
    """0100/0120 -> the JSON body ``POST /decide`` already accepts.

    Raises ``MappingError`` on anything this gateway cannot honestly represent - the
    caller (server.py) turns that into a 96 response, never into a guessed decision.
    """
    missing = [de for de in REQUIRED_REQUEST_FIELDS if de not in msg.fields]
    if missing:
        raise MappingError(f"message is missing required field(s): DE{missing}")

    pan = msg.get(2)
    if not pan.isdigit() or not (12 <= len(pan) <= 19):
        raise MappingError(f"DE2 (PAN) is not a plausible card number (got length "
                           f"{len(pan)})")

    amount_raw = msg.get(4)
    try:
        amount_paise = int(amount_raw)
    except ValueError as exc:
        raise MappingError(f"DE4 (amount) is not numeric: {amount_raw!r}") from exc
    if amount_paise < 0:
        raise MappingError("DE4 (amount) must not be negative")

    ts = _resolve_transmission_ts(msg.get(7), tz_offset_minutes)

    stan = msg.get(11)
    rrn = msg.get(37, "")
    txn_ref = f"ISO8583-{rrn or stan}"

    return {
        "txn_ref": txn_ref,
        "rail": RAIL,
        "debtor_account": mask_pan(pan),
        "creditor_account": msg.get(42, ""),   # card acceptor id (merchant)
        "amount_paise": amount_paise,
        "channel": "pos",
        "device_id": msg.get(41, ""),           # card acceptor terminal id
        "signals": {},
        # Not sent to /decide - kept for building the 0110/0130 response.
        "_stan": stan,
        "_rrn": rrn,
        "_transmission_ts": ts,
    }


def _response_fields(decide_in: dict, code: str) -> dict:
    """The response body shared by every outcome that has a real ``decide_in`` to answer
    from - only the response code (DE39) differs between an approval, a decline, a
    challenge-as-referral, or this gateway's own failure codes."""
    fields = {
        3: "000000",
        4: f"{decide_in['amount_paise']:012d}",
        7: decide_in["_transmission_ts"].astimezone(
            timezone(timedelta(hours=5, minutes=30))).strftime("%m%d%H%M%S"),
        11: decide_in["_stan"],
        39: code,
        41: decide_in["device_id"],
        42: decide_in["creditor_account"],
        49: "356",  # INR
    }
    if decide_in["_rrn"]:
        fields[37] = decide_in["_rrn"]
    return fields


def decide_out_to_iso_response(request_mti: str, decide_in: dict, decide_out: dict) -> Iso8583Message:
    """``DecideOut`` -> an 0110/0130 message. Raises ``MappingError`` rather than
    guessing if /decide returned an action this gateway does not recognise - the caller
    (server.py) turns that into ``malfunction_response``, never into a silent approval."""
    from .spec import REQUEST_TO_RESPONSE_MTI

    response_mti = REQUEST_TO_RESPONSE_MTI.get(request_mti)
    if response_mti is None:
        raise MappingError(f"no response MTI mapped for request MTI {request_mti!r}")

    action = decide_out.get("action", "")
    code = RESPONSE_CODE_FROM_ACTION.get(action)
    if code is None:
        raise MappingError(f"/decide returned an action this gateway cannot map to a "
                           f"response code: {action!r}")
    return Iso8583Message(mti=response_mti, fields=_response_fields(decide_in, code))


def failure_response(request_mti: str, decide_in: dict | None, *, fail_open: bool) -> Iso8583Message:
    """Build a response for the one failure class ``fail_open`` actually governs:
    /decide was unreachable, or the incoming message could not be parsed at all. Never
    used for "an answer came back but this gateway couldn't translate it" - see
    ``malfunction_response`` for that case, which is a defect on Fraud360's side and must
    never be silently approved regardless of this rail's fail policy.
    """
    from .spec import REQUEST_TO_RESPONSE_MTI

    response_mti = REQUEST_TO_RESPONSE_MTI.get(request_mti, "0110")
    if decide_in is None:
        # Couldn't even parse the request far enough to build a normal response body.
        return Iso8583Message(mti=response_mti, fields={
            3: "000000", 4: "000000000000", 7: datetime.now(timezone.utc).strftime(
                "%m%d%H%M%S"), 11: "000000", 39: RESPONSE_CODE_SYSTEM_MALFUNCTION,
            41: "", 42: "", 49: "356"})
    code = RESPONSE_CODE_FROM_ACTION["allow"] if fail_open else RESPONSE_CODE_ISSUER_INOPERATIVE
    return Iso8583Message(mti=response_mti, fields=_response_fields(decide_in, code))


def malfunction_response(request_mti: str, decide_in: dict) -> Iso8583Message:
    """/decide answered, but with something this gateway cannot honestly translate back
    into ISO 8583 (see ``decide_out_to_iso_response``'s ``MappingError``). Always DE39
    96 - deliberately *not* gated by ``fail_open``, because that setting exists for "no
    answer was received," not "an answer was received and this gateway has a bug or an
    unhandled case in interpreting it." Defaulting the latter to an approval would hide
    a real defect behind a code that reads, on every report afterwards, as if the rail's
    own fail-open policy had made a deliberate choice.
    """
    from .spec import REQUEST_TO_RESPONSE_MTI

    response_mti = REQUEST_TO_RESPONSE_MTI.get(request_mti, "0110")
    return Iso8583Message(mti=response_mti,
                          fields=_response_fields(decide_in, RESPONSE_CODE_SYSTEM_MALFUNCTION))
