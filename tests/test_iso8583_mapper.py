"""Coverage for services/iso8583_gateway/app/mapper.py - the DE <-> canonical mapping,
PAN masking, DE7 year inference, and response-code translation."""
from datetime import datetime, timedelta, timezone

import pytest

from services.iso8583_gateway.app.codec import Iso8583Message
from services.iso8583_gateway.app.mapper import (
    MappingError, decide_out_to_iso_response, failure_response,
    iso_request_to_decide_in, mask_pan, _resolve_transmission_ts,
)

IST = timezone(timedelta(hours=5, minutes=30))


def _sample_msg(overrides: dict | None = None) -> Iso8583Message:
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
    fields.update(overrides or {})
    return Iso8583Message(mti="0100", fields=fields)


# --------------------------------------------------------------------------- mask_pan

def test_mask_pan_never_contains_the_raw_pan():
    pan = "4111111111111111"
    masked = mask_pan(pan)
    assert pan not in masked
    assert masked.startswith("PANHASH-")
    assert masked.endswith(pan[-4:])


def test_mask_pan_is_stable_for_the_same_pan():
    pan = "4111111111111111"
    assert mask_pan(pan) == mask_pan(pan)


def test_mask_pan_differs_for_different_pans():
    assert mask_pan("4111111111111111") != mask_pan("4111111111111112")


# --------------------------------------------------------------------------- DE7

def test_de7_same_day_resolves_to_today_in_configured_offset():
    now = datetime.now(IST)
    mmddhhmmss = now.strftime("%m%d%H%M%S")
    resolved = _resolve_transmission_ts(mmddhhmmss, tz_offset_minutes=330)
    assert resolved.tzinfo == timezone.utc
    local_again = resolved.astimezone(IST)
    assert (local_again.month, local_again.day) == (now.month, now.day)


def test_de7_new_year_wraparound_rolls_back_a_year():
    # "Now" is Jan 1st just after midnight IST; DE7 reads Dec 31 - must resolve to last
    # year's Dec 31, not a Dec 31 nine months in the future.
    fixed_now = datetime(2026, 1, 1, 0, 5, 0, tzinfo=IST)

    import services.iso8583_gateway.app.mapper as mapper_mod

    class _FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return fixed_now if tz else fixed_now.replace(tzinfo=None)

    original = mapper_mod.datetime
    mapper_mod.datetime = _FrozenDatetime
    try:
        resolved = mapper_mod._resolve_transmission_ts("1231235500", tz_offset_minutes=330)
    finally:
        mapper_mod.datetime = original

    local = resolved.astimezone(IST)
    assert (local.year, local.month, local.day) == (2025, 12, 31)


def test_de7_malformed_raises_mapping_error():
    with pytest.raises(MappingError):
        _resolve_transmission_ts("notadatetime", tz_offset_minutes=330)


def test_de7_invalid_calendar_date_raises_mapping_error():
    with pytest.raises(MappingError):
        _resolve_transmission_ts("0230000000", tz_offset_minutes=330)  # Feb 30


# --------------------------------------------------------------------------- request mapping

def test_happy_path_maps_every_field():
    msg = _sample_msg()
    decide_in = iso_request_to_decide_in(msg, tz_offset_minutes=330)
    assert decide_in["rail"] == "CARD"
    assert decide_in["amount_paise"] == 1899900
    assert decide_in["debtor_account"] == mask_pan("4111111111111111")
    assert decide_in["creditor_account"] == "MERCH-88213-EL"
    assert decide_in["device_id"] == "TERM0001"
    assert decide_in["channel"] == "pos"
    assert decide_in["txn_ref"] == "ISO8583-24010112345"
    assert decide_in["signals"] == {}


def test_txn_ref_falls_back_to_stan_when_rrn_absent():
    msg = _sample_msg()
    del msg.fields[37]
    decide_in = iso_request_to_decide_in(msg, tz_offset_minutes=330)
    assert decide_in["txn_ref"] == "ISO8583-004417"


def test_missing_required_field_raises_mapping_error():
    msg = _sample_msg()
    del msg.fields[4]
    with pytest.raises(MappingError, match=r"DE\[4\]"):
        iso_request_to_decide_in(msg, tz_offset_minutes=330)


def test_implausible_pan_length_rejected():
    msg = _sample_msg({2: "123"})
    with pytest.raises(MappingError, match="PAN"):
        iso_request_to_decide_in(msg, tz_offset_minutes=330)


def test_non_numeric_amount_rejected():
    msg = _sample_msg({4: "notanumber!!"})
    with pytest.raises(MappingError, match="DE4"):
        iso_request_to_decide_in(msg, tz_offset_minutes=330)


# --------------------------------------------------------------------------- response mapping

def _decide_in_from(msg):
    return iso_request_to_decide_in(msg, tz_offset_minutes=330)


@pytest.mark.parametrize("action,expected_code", [
    ("allow", "00"), ("decline", "05"), ("challenge", "01"),
])
def test_action_maps_to_documented_response_code(action, expected_code):
    msg = _sample_msg()
    decide_in = _decide_in_from(msg)
    decide_out = {"action": action}
    resp = decide_out_to_iso_response(msg.mti, decide_in, decide_out)
    assert resp.mti == "0110"
    assert resp.fields[39] == expected_code


def test_response_echoes_stan_and_rrn():
    msg = _sample_msg()
    decide_in = _decide_in_from(msg)
    resp = decide_out_to_iso_response(msg.mti, decide_in, {"action": "allow"})
    assert resp.fields[11] == "004417"
    assert resp.fields[37] == "24010112345"


def test_unknown_action_from_decide_raises_rather_than_guesses():
    msg = _sample_msg()
    decide_in = _decide_in_from(msg)
    with pytest.raises(MappingError, match="cannot map"):
        decide_out_to_iso_response(msg.mti, decide_in, {"action": "quarantine"})


def test_advice_mti_maps_to_advice_response_mti():
    msg = _sample_msg()
    msg.mti = "0120"
    decide_in = _decide_in_from(_sample_msg())  # transmission ts computed against 0100 fields
    resp = decide_out_to_iso_response("0120", decide_in, {"action": "allow"})
    assert resp.mti == "0130"


# --------------------------------------------------------------------------- failure_response

def test_failure_response_with_no_decide_in_uses_system_malfunction():
    resp = failure_response("0100", None, fail_open=True)
    assert resp.fields[39] == "96"


def test_failure_response_fail_open_approves():
    msg = _sample_msg()
    decide_in = _decide_in_from(msg)
    resp = failure_response(msg.mti, decide_in, fail_open=True)
    assert resp.fields[39] == "00"


def test_failure_response_fail_closed_declines_as_issuer_inoperative():
    msg = _sample_msg()
    decide_in = _decide_in_from(msg)
    resp = failure_response(msg.mti, decide_in, fail_open=False)
    assert resp.fields[39] == "91"
