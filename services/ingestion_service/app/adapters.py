"""Rail adapters: bank payment formats in, one canonical transaction out.

A bank does not have "transactions", it has UPI collect requests, IMPS P2A transfers, NEFT
batch items, RTGS messages and card authorisations - different field names, different
identifiers, different notions of amount and time. Detection must not care. Every rule in
the EWS catalogue is written against one canonical shape, so the mapping happens exactly
once, here, at the edge.

An adapter is deliberately dumb: rename, convert, validate. No enrichment, no scoring, no
opinion about risk. When a bank's UPI feed changes shape, the blast radius is one function
and one test - not the detection engine, not the dashboards, not the metric registry.

Money is integer paise throughout. Rails quote amounts in rupees as decimal strings, and
parsing those into floats is how a reconciliation that must tie exactly starts drifting by
a paisa per thousand transactions.
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Callable

RAILS = ("UPI", "IMPS", "NEFT", "RTGS", "CARD")

#: Canonical fields every adapter must produce.
REQUIRED = ("txn_id", "ts", "rail", "amount_paise", "debtor_account", "creditor_account")


class AdapterError(ValueError):
    """The payload cannot be mapped. Rejected at the edge, with the reason."""


def to_paise(value: Any) -> int:
    """Rupees (string, int or Decimal) to integer paise, without touching a float.

    ``Decimal("1234.55")`` is exact; ``float("1234.55") * 100`` is 123454.99999999999.
    Over a day's volume that difference is the reconciliation failing for no reason
    anyone can find.
    """
    if isinstance(value, bool):
        raise AdapterError("amount must be a number, not a boolean")
    if value is None:
        raise AdapterError("amount is required")
    try:
        rupees = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise AdapterError(f"amount is not a number: {value!r}") from exc
    if rupees < 0:
        raise AdapterError("amount must not be negative")
    paise = (rupees * 100).to_integral_value()
    if paise != rupees * 100:
        raise AdapterError(f"amount {value!r} is finer than one paisa")
    return int(paise)


def to_utc(value: Any) -> datetime:
    if isinstance(value, datetime):
        dt = value
    else:
        text = str(value or "").strip()
        if not text:
            raise AdapterError("timestamp is required")
        try:
            dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError as exc:
            raise AdapterError(f"timestamp is not ISO-8601: {value!r}") from exc
    # A naive timestamp from an Indian core banking system is IST, but guessing is worse
    # than refusing: a five-and-a-half hour error silently breaks every time-window rule.
    if dt.tzinfo is None:
        raise AdapterError(
            "timestamp has no timezone. Send an offset (e.g. +05:30) - a naive time "
            "cannot be placed on the clock that the velocity rules measure against.")
    return dt.astimezone(timezone.utc)


def _pick(payload: dict, *names: str, required: bool = True, default: Any = None) -> Any:
    for n in names:
        if n in payload and payload[n] not in (None, ""):
            return payload[n]
    if required:
        raise AdapterError("missing required field, one of: " + ", ".join(names))
    return default


def _score_or_none(payload: dict, *names: str, field: str) -> float | None:
    """A 0.0-1.0 risk score from an external provider (device posture, behavioural
    biometrics) - absent when the rail/session does not carry one, which is the normal
    case until a tenant has that integration wired up. Present-but-out-of-range is
    rejected rather than clamped: a provider sending 1.5 has a bug worth surfacing, not
    silently hiding under a score that looks plausible."""
    value = _pick(payload, *names, required=False, default=None)
    if value is None:
        return None
    try:
        score = float(value)
    except (TypeError, ValueError) as exc:
        raise AdapterError(f"{field} is not a number: {value!r}") from exc
    if not (0.0 <= score <= 1.0):
        raise AdapterError(f"{field} must be between 0.0 and 1.0, got {score!r}")
    return score


def _common(payload: dict) -> dict:
    """Fields that mean the same thing on every rail."""
    return {
        "branch": str(_pick(payload, "branch", "branch_code", required=False, default="")),
        "region": str(_pick(payload, "region", required=False, default="")).lower(),
        "product": str(_pick(payload, "product", "account_type",
                             required=False, default="savings")).lower(),
        "customer_segment": str(_pick(payload, "customer_segment", "segment",
                                      required=False, default="retail")).lower(),
        "device_id": str(_pick(payload, "device_id", "device", "terminal_id",
                               required=False, default="")),
        "ip_addr": str(_pick(payload, "ip_addr", "ip", required=False, default="")),
        "status": str(_pick(payload, "status", required=False, default="settled")).lower(),
        # Detection inputs the rails may or may not carry. Absent is honest; invented
        # would make a rule look like it is working when it is not.
        "beneficiary_added_ts": (to_utc(payload["beneficiary_added_ts"])
                                 if payload.get("beneficiary_added_ts") else None),
        "direction": str(_pick(payload, "direction", required=False, default="debit")).lower(),
        # AI/ML roadmap Phase 1 (BRD OD-06/s19): device-posture and behavioural-biometric
        # risk scores from an integrated provider (VideoPD, Human Fraud Detection
        # Framework). No rail sends these today - absent is the normal case, not a gap
        # in this adapter - see CHN-04/CHN-05 in the EWS catalogue and
        # features.NEEDS_EXTERNAL_DATA.
        "device_risk_score": _score_or_none(payload, "device_risk_score",
                                            field="device_risk_score"),
        "behavior_anomaly_score": _score_or_none(payload, "behavior_anomaly_score",
                                                 "behaviour_anomaly_score",
                                                 field="behavior_anomaly_score"),
    }


def upi(payload: dict) -> dict:
    """NPCI UPI. Identifiers are VPAs; the account behind them is what detection needs."""
    return {
        "txn_id": str(_pick(payload, "txn_id", "upiTransactionId", "npciTxnId")),
        "ts": to_utc(_pick(payload, "ts", "txnTimestamp", "timestamp")),
        "rail": "UPI",
        "amount_paise": to_paise(_pick(payload, "amount", "amountRupees", "txnAmount")),
        "debtor_account": str(_pick(payload, "debtor_account", "payerAccount", "payerVpa")),
        "creditor_account": str(_pick(payload, "creditor_account", "payeeAccount", "payeeVpa")),
        "channel": str(payload.get("channel") or "mobile").lower(),
        **_common(payload),
    }


def imps(payload: dict) -> dict:
    return {
        "txn_id": str(_pick(payload, "txn_id", "rrn", "imps_rrn")),
        "ts": to_utc(_pick(payload, "ts", "txnDate", "timestamp")),
        "rail": "IMPS",
        "amount_paise": to_paise(_pick(payload, "amount", "txnAmount")),
        "debtor_account": str(_pick(payload, "debtor_account", "remitterAccount")),
        "creditor_account": str(_pick(payload, "creditor_account", "beneficiaryAccount")),
        "channel": str(payload.get("channel") or "netbanking").lower(),
        **_common(payload),
    }


def neft(payload: dict) -> dict:
    """NEFT settles in batches; the batch time is not the customer's instruction time."""
    return {
        "txn_id": str(_pick(payload, "txn_id", "utr", "neft_utr")),
        "ts": to_utc(_pick(payload, "ts", "instructionTime", "batchTime", "timestamp")),
        "rail": "NEFT",
        "amount_paise": to_paise(_pick(payload, "amount", "txnAmount")),
        "debtor_account": str(_pick(payload, "debtor_account", "senderAccount")),
        "creditor_account": str(_pick(payload, "creditor_account", "beneficiaryAccount")),
        "channel": str(payload.get("channel") or "netbanking").lower(),
        **_common(payload),
    }


def rtgs(payload: dict) -> dict:
    return {
        "txn_id": str(_pick(payload, "txn_id", "utr", "rtgs_utr")),
        "ts": to_utc(_pick(payload, "ts", "settlementTime", "timestamp")),
        "rail": "RTGS",
        "amount_paise": to_paise(_pick(payload, "amount", "txnAmount")),
        "debtor_account": str(_pick(payload, "debtor_account", "senderAccount")),
        "creditor_account": str(_pick(payload, "creditor_account", "beneficiaryAccount")),
        "channel": str(payload.get("channel") or "branch").lower(),
        **_common(payload),
    }


def card(payload: dict) -> dict:
    """Card authorisations name a merchant, not an account. Detection still needs two
    sides, so the merchant id stands in as the counterparty."""
    return {
        "txn_id": str(_pick(payload, "txn_id", "authCode", "arn")),
        "ts": to_utc(_pick(payload, "ts", "authTime", "timestamp")),
        "rail": "CARD",
        "amount_paise": to_paise(_pick(payload, "amount", "authAmount")),
        "debtor_account": str(_pick(payload, "debtor_account", "cardAccount", "pan_token")),
        "creditor_account": str(_pick(payload, "creditor_account", "merchantId", "merchant_id")),
        "channel": str(payload.get("channel") or "pos").lower(),
        **_common(payload),
    }


ADAPTERS: dict[str, Callable[[dict], dict]] = {
    "UPI": upi, "IMPS": imps, "NEFT": neft, "RTGS": rtgs, "CARD": card,
}


def adapt(rail: str, payload: dict) -> dict:
    rail = (rail or "").upper()
    fn = ADAPTERS.get(rail)
    if fn is None:
        raise AdapterError(f"unsupported rail '{rail}'. Supported: " + ", ".join(RAILS))
    out = fn(payload)
    missing = [f for f in REQUIRED if out.get(f) in (None, "")]
    if missing:
        raise AdapterError("missing after mapping: " + ", ".join(missing))
    if out["debtor_account"] == out["creditor_account"]:
        raise AdapterError("debtor and creditor accounts are identical")
    return out
