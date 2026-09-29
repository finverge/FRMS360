"""Adapting a CBS extract row into the canonical event shape.

Same discipline as the payment adapters: integer paise via Decimal, timezone-aware
timestamps, and a refusal rather than a default when a field that changes the meaning of
the event is missing. A repayment whose funding source is absent is *not* an own-funds
repayment - it is an unknown one, and BEH-02 must not read it as clean.
"""
from __future__ import annotations

from typing import Any

from .adapters import AdapterError, to_paise, to_utc
from .cbs_models import CASH_DIRECTIONS, EVENT_KINDS, FUNDING_SOURCES


def _pick(payload: dict, *names: str, required: bool = True, default: Any = None) -> Any:
    for n in names:
        if n in payload and payload[n] not in (None, ""):
            return payload[n]
    if required:
        raise AdapterError(f"missing required field, one of: {', '.join(names)}")
    return default


def _bool_or_none(value: Any) -> bool | None:
    """Tri-state on purpose.

    ``None`` means the CBS did not say, which is different from saying no. Coercing an
    absent flag to False would make BEH-03 report every sale as correctly routed.
    """
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return value
    s = str(value).strip().lower()
    if s in ("true", "t", "yes", "y", "1"):
        return True
    if s in ("false", "f", "no", "n", "0"):
        return False
    raise AdapterError(f"not a boolean: {value!r}")


def adapt_cbs(payload: dict) -> dict:
    kind = str(_pick(payload, "kind", "event_type", "eventType")).strip().lower()
    if kind not in EVENT_KINDS:
        raise AdapterError(
            f"unknown CBS event kind '{kind}'. One of: {', '.join(sorted(EVENT_KINDS))}")

    out = {
        "source_event_id": str(_pick(payload, "event_id", "eventId", "cbsRef")),
        "kind": kind,
        "ts": to_utc(_pick(payload, "ts", "timestamp", "eventDate")),
        "account": str(_pick(payload, "account", "loanAccount", "accountNumber")),
        "counterparty_account": str(_pick(payload, "counterparty_account", "payeeAccount",
                                          "beneficiaryAccount", required=False,
                                          default="")),
        "counterparty_name": str(_pick(payload, "counterparty_name", "payeeName",
                                       required=False, default=""))[:200],
        "amount_paise": 0,
        "funding_source": "",
        "routed_through_lender": _bool_or_none(
            _pick(payload, "routed_through_lender", "routedThroughLender",
                  required=False, default=None)),
        "within_sanctioned_purpose": _bool_or_none(
            _pick(payload, "within_sanctioned_purpose", "withinSanctionedPurpose",
                  required=False, default=None)),
        "attributes": {k: v for k, v in payload.items()
                       if k not in ("kind", "event_type", "eventType")},
    }

    # An observation carries no money; everything else does.
    if kind != "rm_observation" and kind != "account_status":
        out["amount_paise"] = to_paise(
            _pick(payload, "amount", "amount_paise", "txnAmount"))
        if out["amount_paise"] < 0:
            raise AdapterError("amount cannot be negative")

    if kind == "loan_repayment":
        src = str(_pick(payload, "funding_source", "fundingSource",
                        required=False, default="unknown")).strip().lower()
        if src not in FUNDING_SOURCES:
            raise AdapterError(
                f"unknown funding source '{src}'. One of: {', '.join(FUNDING_SOURCES)}")
        out["funding_source"] = src

    if kind == "sale_proceeds" and out["routed_through_lender"] is None:
        # The single fact this event exists to carry. Without it the row says nothing,
        # and quarantining it is more useful than storing an unusable record.
        raise AdapterError(
            "sale_proceeds requires routed_through_lender; without it the event cannot "
            "answer whether proceeds reached the lender")

    if kind == "cash_transaction":
        # The fact CTR aggregation exists to carry: deposits and withdrawals both count
        # toward the same account-month total (PMLA Rule 3), but which one this row is
        # cannot be inferred from amount and account alone.
        direction = str(_pick(payload, "direction", "txn_type", "type",
                              required=False, default="")).strip().lower()
        if direction not in CASH_DIRECTIONS:
            raise AdapterError(
                f"cash_transaction requires direction to be one of "
                f"{', '.join(CASH_DIRECTIONS)}, got {direction!r}")
        out["attributes"]["direction"] = direction

    if kind == "collateral_valuation":
        # The two facts LOS-02 exists to carry: what kind of asset, and who valued it.
        # Neither can be recovered later - a valuation with no valuer attributed can
        # never answer "which valuer is inflating", which is the entire question.
        asset_type = str(_pick(payload, "asset_type", "assetType",
                               required=False, default="")).strip().lower()
        valuer_id = str(_pick(payload, "valuer_id", "valuerId",
                              required=False, default="")).strip()
        if not asset_type:
            raise AdapterError(
                "collateral_valuation requires asset_type; without it this valuation "
                "cannot be grouped with comparable ones to judge whether it is in line")
        if not valuer_id:
            raise AdapterError(
                "collateral_valuation requires valuer_id; without it a deviation can "
                "never be attributed to the valuer who produced it")
        out["attributes"]["asset_type"] = asset_type
        out["attributes"]["valuer_id"] = valuer_id

    return out
