"""Adapting a CBS extract row into the canonical event shape.

Same discipline as the payment adapters: integer paise via Decimal, timezone-aware
timestamps, and a refusal rather than a default when a field that changes the meaning of
the event is missing. A repayment whose funding source is absent is *not* an own-funds
repayment - it is an unknown one, and BEH-02 must not read it as clean.
"""
from __future__ import annotations

from datetime import date
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

        # Optional - most CBS extracts today send only the repayment itself, not the
        # instalment schedule it was due against. Absent means CBS-08 stays exactly as
        # unmeasurable as it already is; this never fabricates a due date to fill the
        # gap. A calendar date, not a timestamp - "due" has no time-of-day meaning, and
        # requiring one via to_utc() would force a fake time onto every CBS extract that
        # (reasonably) only carries a due date.
        due_raw = _pick(payload, "due_date", "dueDate", required=False, default="")
        if due_raw:
            try:
                due = due_raw if isinstance(due_raw, date) else date.fromisoformat(str(due_raw))
            except ValueError as exc:
                raise AdapterError(f"due_date is not ISO-8601 (YYYY-MM-DD): "
                                   f"{due_raw!r}") from exc
            out["attributes"]["due_date"] = due.isoformat()

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

    if kind == "od_position":
        # The single fact CBS-05 exists to carry: what the limit was, so the amount above
        # (the balance drawn) can be turned into a ratio. Without it the event cannot say
        # whether the draw was a breach or an unremarkable working-capital swing.
        # Same rupees-in, paise-out convention as the top-level "amount" field above -
        # the stored attribute is named "_paise" because that is what it holds, not
        # because that is what the CBS extract is expected to send.
        limit_paise = to_paise(_pick(payload, "sanctioned_limit", "sanctionedLimit",
                                     "odLimit", "limit", required=False, default=0))
        if limit_paise <= 0:
            raise AdapterError(
                "od_position requires a positive sanctioned_limit; without it "
                "CBS-05 cannot compute a breach ratio")
        out["attributes"]["sanctioned_limit_paise"] = limit_paise

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

        # Optional - most CBS/LOS extracts won't send this yet, since it needs a
        # specific, CERSAI-matchable asset identifier (a property/vehicle registration
        # number, not a category like asset_type above), a fact most source systems
        # don't carry per valuation today. Absent means CPT-03 stays exactly as
        # unmeasurable as it already is - this never fabricates one to fill the gap.
        # See docs/rbi_ews_mapping.py's note on CPT-03/CERSAI for why this alone does
        # not guarantee a real bank's identifier will match a real CERSAI extract's key
        # format - that alignment can only be confirmed against real pilot data.
        collateral_id = str(_pick(payload, "collateral_id", "charge_reference",
                                  "chargeReference", required=False, default="")).strip()
        if collateral_id:
            out["attributes"]["collateral_id"] = collateral_id

    # bg_lc_event needs no extra field, the same shape as cheque_return (CBS-04): the
    # event's own existence is the fact CBS-06 counts, not any attribute of it.

    if kind == "facility_sanction":
        # The single fact CBS-07 exists to carry: sanctioning a facility is routine
        # business, only sanctioning one specifically to fund interest on an existing
        # exposure is the red flag. Without this flag the event cannot say which kind of
        # sanction it was, so - same reasoning as sale_proceeds's routed_through_lender -
        # it is refused rather than silently counted as an ordinary sanction.
        funds_interest = _bool_or_none(
            _pick(payload, "funds_interest", "fundsInterest", required=False, default=None))
        if funds_interest is None:
            raise AdapterError(
                "facility_sanction requires funds_interest; without it the event cannot "
                "say whether this sanction was to fund interest on existing exposure")
        out["attributes"]["funds_interest"] = funds_interest

    return out
