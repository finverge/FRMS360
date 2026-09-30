"""Map a borrower's own line-item wording to the platform's canonical metric codes.

No two borrowers' statements use the same words for the same line - one balance sheet
says "Trade Receivables", another says "Sundry Debtors", a third says "Accounts
Receivable (net)". Signal computation needs one name per concept regardless of source
wording, the same reason ``cbs_features.py``'s five ratios read a fixed set of CBS event
kinds rather than whatever a bank's own extract happens to call them.

Deliberately conservative: an unmatched line item is dropped, not guessed at. A metric
that never appears is later reported unmeasurable by the signal engine (never scored as
zero) - matching the observe-only-what-exists discipline every other feature module in
this platform already applies.
"""
from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation

#: Canonical metric codes the signal engine understands. Order matters only for
#: readability; matching tries every pattern regardless of position.
_PATTERNS: list[tuple[str, str]] = [
    (r"total\s+income|total\s+revenue|revenue\s+from\s+operations|^revenue$|^sales$",
     "REVENUE"),
    (r"inventor(y|ies)|stock[\s-]in[\s-]trade|closing\s+stock", "INVENTORY"),
    (r"trade\s+receivable|accounts?\s+receivable|sundry\s+debtors?", "AR"),
    (r"other\s+current\s+assets?|^oca$", "OCA"),
    (r"short[\s-]term\s+borrowing|working\s+capital\s+(loan|borrowing)|cash\s+credit|"
     r"overdraft", "WC_BORROWING"),
    (r"fixed\s+assets?|property,?\s+plant\s+and\s+equipment|^ppe$|net\s+block",
     "FIXED_ASSETS"),
    (r"long[\s-]term\s+(debt|borrowing)|term\s+loan", "LT_DEBT"),
    (r"shareholders?\'?\s+equity|total\s+equity|net\s+worth|paid[\s-]up\s+capital",
     "EQUITY"),
    (r"^cash\s+and\s+cash\s+equivalents?$|^cash\s+(in\s+hand|at\s+bank)?$|^cash$",
     "CASH"),
    (r"ebitda|operating\s+profit", "EBITDA"),
    (r"unbilled\s+revenue|contract\s+assets?", "UNBILLED_REVENUE"),
    (r"contingent\s+liabilit(y|ies)", "CONTINGENT_LIABILITIES"),
    (r"cash\s+collect(ed|ions?)\s+from\s+(operations|customers)|"
     r"net\s+cash\s+from\s+operating\s+activities", "CASH_COLLECTED"),
]

_COMPILED = [(re.compile(pat, re.IGNORECASE), code) for pat, code in _PATTERNS]

ALL_METRIC_CODES = sorted({code for _, code in _PATTERNS})


def metric_code_for(line_item: str) -> str | None:
    """The canonical metric code for a line-item label, or None if unrecognised."""
    text = line_item.strip()
    for pattern, code in _COMPILED:
        if pattern.search(text):
            return code
    return None


def to_paise(value: str | float | Decimal) -> int | None:
    """Rupees (crore/lakh-formatted or plain, with commas) to integer paise.

    Returns None rather than raising on a value that cannot be parsed - a single
    unparseable cell in a 40-row table should not fail the whole extraction, the same
    partial-acceptance discipline ``file_model.py``'s batch intake applies to a bad row.
    """
    if isinstance(value, (int, float, Decimal)):
        try:
            return int(Decimal(str(value)) * 100)
        except (InvalidOperation, ValueError):
            return None
    text = str(value).strip()
    if not text:
        return None
    negative = text.startswith("(") and text.endswith(")")
    text = text.strip("()").replace(",", "").replace("₹", "").strip()
    try:
        rupees = Decimal(text)
    except InvalidOperation:
        return None
    paise = int(rupees * 100)
    return -paise if negative else paise
