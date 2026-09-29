"""PII masking for analytical output.

Masking happens **server-side**. Masking in the browser would be theatre: the clear value
would already have crossed the network and would sit in the client's memory, cache and
devtools. Nothing leaves this process unmasked unless the caller both holds the reveal
capability and supplies a justification, which is audited.

Under DPDP the analytics surface is subject to the same purpose-limitation and
data-minimisation duties as any other processing, so masked-by-default is the correct
posture even for internal users.
"""
from typing import Any

# Column -> masking style. Anything not listed is not considered personal data.
ACCOUNT_FIELDS = {"debtor_account", "creditor_account", "account"}
DEVICE_FIELDS = {"device_id"}
IP_FIELDS = {"ip_addr"}
EMAILISH_FIELDS = {"analyst", "assignee"}

PII_FIELDS = ACCOUNT_FIELDS | DEVICE_FIELDS | IP_FIELDS


def _mask_tail(value: str, keep_prefix: int = 2, keep_tail: int = 4) -> str:
    """Keep enough to correlate rows, not enough to identify the customer."""
    if not value or len(value) <= keep_prefix + keep_tail:
        return "•" * len(value or "")
    return f"{value[:keep_prefix]}{'•' * (len(value) - keep_prefix - keep_tail)}{value[-keep_tail:]}"


def _mask_ip(value: str) -> str:
    parts = (value or "").split(".")
    return ".".join(parts[:2] + ["•", "•"]) if len(parts) == 4 else "•••"


def mask_value(field: str, value: Any) -> Any:
    if value is None:
        return None
    if field in ACCOUNT_FIELDS:
        return _mask_tail(str(value))
    if field in DEVICE_FIELDS:
        return _mask_tail(str(value), keep_prefix=1, keep_tail=3)
    if field in IP_FIELDS:
        return _mask_ip(str(value))
    return value


def mask_row(row: dict, reveal: bool) -> dict:
    if reveal:
        return row
    return {k: (mask_value(k, v) if k in PII_FIELDS else v) for k, v in row.items()}


def mask_rows(rows: list[dict], reveal: bool) -> list[dict]:
    return rows if reveal else [mask_row(r, False) for r in rows]


def mask_payload(obj: Any, reveal: bool) -> Any:
    """Recursively mask a nested evidence payload."""
    if reveal:
        return obj
    if isinstance(obj, dict):
        return {k: (mask_value(k, v) if k in PII_FIELDS else mask_payload(v, False))
                for k, v in obj.items()}
    if isinstance(obj, list):
        return [mask_payload(x, False) for x in obj]
    return obj


def fields_present(obj: Any) -> list[str]:
    """Which PII fields a payload actually carried - recorded in the audit entry so the
    trail says what was exposed, not merely that something was."""
    found: set[str] = set()

    def walk(o: Any) -> None:
        if isinstance(o, dict):
            for k, v in o.items():
                if k in PII_FIELDS and v is not None:
                    found.add(k)
                walk(v)
        elif isinstance(o, list):
            for x in o:
                walk(x)

    walk(obj)
    return sorted(found)
