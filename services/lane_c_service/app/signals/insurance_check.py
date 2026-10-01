"""LNC-17 - under- or over-insured inventory (RBI 2016 illustrative signal #7).

Cross-checks a new reference feed (kind ``insurance_coverage``) against the ``INVENTORY``
figure Lane C already extracts from the borrower's own statement - the interesting half
of this signal is data Lane C has had since day one; only the insured-value side is new.

**Feed contract** for the ``insurance_coverage`` kind's entry attributes:
``{"insured_value_paise": int, "policy_number": str}``.

Both directions matter and for different reasons: under-insurance means a real loss
would not be covered; over-insurance is a classic inflated-claim motive. Neither is
assumed - the ratio is reported either way and the evidence says which direction fired.
"""
from __future__ import annotations

from .signal_engine import SignalResult, _unmeasurable, band

#: Below this fraction of inventory value insured, a real loss event would leave a real
#: gap. Above the upper bound, the cover exceeds what could plausibly be claimed for -
#: itself a red flag, not a comfort.
_UNDER_INSURED_BELOW = 0.7
_OVER_INSURED_ABOVE = 1.5


def compute_insurance_coverage_check(company_identifier: str | None,
                                     insurance_entry: dict | None,
                                     inventory_value_paise: int | None) -> SignalResult:
    code = "LNC-17"
    if not company_identifier:
        return _unmeasurable(code, "no CIN/PAN on file for this borrower")
    if insurance_entry is None:
        return _unmeasurable(code, "no active insurance_coverage feed loaded, or this "
                             "borrower is not in the loaded list")
    if not inventory_value_paise:
        return _unmeasurable(code, "no inventory figure found in this statement")

    attrs = insurance_entry.get("attributes") or {}
    insured = attrs.get("insured_value_paise")
    if not isinstance(insured, (int, float)) or insured <= 0:
        return _unmeasurable(code, "feed entry has no valid insured_value_paise")

    ratio = insured / inventory_value_paise
    if ratio < _UNDER_INSURED_BELOW:
        severity = 35
        evidence = (f"Inventory is under-insured: cover is {ratio:.0%} of the stated "
                    f"inventory value.")
    elif ratio > _OVER_INSURED_ABOVE:
        severity = 30
        evidence = (f"Inventory is over-insured: cover is {ratio:.0%} of the stated "
                    f"inventory value.")
    else:
        return SignalResult(code, ratio, 1.0, "pass", 0,
                            f"Insurance cover ({ratio:.0%} of inventory value) is in a "
                            f"reasonable range.", evidence_basis="ratio")

    return SignalResult(code, ratio, 1.0, band(severity), severity, evidence,
                        evidence_basis="ratio")
