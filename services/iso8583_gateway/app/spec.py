"""ISO 8583:1987 field definitions — the standard/public subset this gateway needs.

Only the data elements Fraud360's canonical decision request actually uses are defined
here. This is deliberate, not an oversight: DE48, DE62 and DE120-127 (and several others)
are "private use" in the 1987 standard, meaning every network and every switch vendor
fills them differently. Defining them generically here would mean guessing at a specific
vendor's dialect and shipping that guess as if it were the standard - exactly the kind of
invented fact this platform's documentation discipline exists to avoid. See
docs/ISO8583_LANE_A_SCOPING.md §4 and §7: private-field mapping is a per-deployment
extension point (``EXTRA_FIELDS`` below), confirmed against each pilot switch's own spec
document, not something this module asserts on their behalf.

Encoding: ASCII only, matching the great majority of TCP/IP switch links. BCD-encoded
fields are a documented future extension (see the scoping doc), not built here.
"""
from __future__ import annotations

from dataclasses import dataclass

FIXED = "fixed"
LLVAR = "llvar"
LLLVAR = "lllvar"

NUMERIC = "n"
ALPHA = "a"
ALPHANUMERIC = "an"
ALPHANUMERIC_SPECIAL = "ans"


@dataclass(frozen=True)
class FieldDef:
    de: int
    kind: str          # FIXED | LLVAR | LLLVAR
    fmt: str            # NUMERIC | ALPHA | ALPHANUMERIC | ALPHANUMERIC_SPECIAL
    length: int          # fixed width, or the max width for LLVAR/LLLVAR
    name: str


#: The standard, publicly-defined data elements this gateway maps to/from the canonical
#: decision request. Every entry here is a stable ISO 8583:1987 definition, not a
#: vendor-specific one - see the module docstring.
STANDARD_FIELDS: dict[int, FieldDef] = {
    2:  FieldDef(2,  LLVAR, NUMERIC, 19, "Primary account number (PAN)"),
    3:  FieldDef(3,  FIXED, NUMERIC, 6,  "Processing code"),
    4:  FieldDef(4,  FIXED, NUMERIC, 12, "Amount, transaction"),
    7:  FieldDef(7,  FIXED, NUMERIC, 10, "Transmission date & time (MMDDhhmmss, no year)"),
    11: FieldDef(11, FIXED, NUMERIC, 6,  "System trace audit number (STAN)"),
    32: FieldDef(32, LLVAR, NUMERIC, 11, "Acquiring institution identification code"),
    37: FieldDef(37, FIXED, ALPHANUMERIC, 12, "Retrieval reference number (RRN)"),
    39: FieldDef(39, FIXED, ALPHANUMERIC, 2,  "Response code"),
    41: FieldDef(41, FIXED, ALPHANUMERIC_SPECIAL, 8,  "Card acceptor terminal id"),
    42: FieldDef(42, FIXED, ALPHANUMERIC_SPECIAL, 15, "Card acceptor identification code"),
    49: FieldDef(49, FIXED, NUMERIC, 3,  "Currency code, transaction"),
}

#: Data elements a request message must carry for this gateway to accept it. DE39
#: (response code) is a response-only field and deliberately absent from this set.
REQUIRED_REQUEST_FIELDS = (2, 3, 4, 7, 11, 41, 42, 49)

#: Authorisation request / response MTIs this gateway understands. Reversals, advices,
#: network management (08xx) and batch/file MTIs are out of scope for this component -
#: it is a Lane A real-time front door, not a general 8583 switch.
MTI_AUTH_REQUEST = "0100"
MTI_AUTH_REQUEST_ADVICE = "0120"
MTI_AUTH_RESPONSE = "0110"
MTI_AUTH_RESPONSE_ADVICE = "0130"

REQUEST_TO_RESPONSE_MTI = {
    MTI_AUTH_REQUEST: MTI_AUTH_RESPONSE,
    MTI_AUTH_REQUEST_ADVICE: MTI_AUTH_RESPONSE_ADVICE,
}


class Iso8583SpecError(ValueError):
    """A message could not be built or parsed against ``STANDARD_FIELDS``."""
