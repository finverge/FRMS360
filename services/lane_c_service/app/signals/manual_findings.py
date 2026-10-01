"""LNC-15/LNC-16/LNC-21 - RBI 2016 signals that are physical, qualitative facts no
document or feed can ever carry automatically: a godown inspection postponed for reasons
that didn't hold up (#12), original bills the borrower could not produce for
verification (#25), and invoices lacking TAN/other required particulars (#8) - the last
one needing a human to actually look at a sampled invoice, not something the extraction
pipeline's balance-sheet parser can check.

Lane B declares the same shape already - QUAL-01/02/03 in
services/config_service/app/ews_catalogue.py are marked ``"qualitative": True`` and
deliberately never auto-scored - but no actual entry point for a human to record one
exists anywhere in this codebase. These two signals, and the ``manual-finding`` API route
that feeds them (routes/lane_c.py), are that entry point for Lane C.

Same pure-function shape as roc_mca_check.py/rating_check.py: the caller (runner.py)
looks up the ManualFinding row for this exact (tenant, account, reporting_date,
signal_code) and passes the result in - no I/O here.
"""
from __future__ import annotations

from .signal_engine import SignalResult, _unmeasurable, band

#: A recorded red flag from a human on-site is treated with the same weight this
#: catalogue already gives a critical documentary finding (LNC-01's statutory-dues
#: language, LNC-10's ROC liability) - a credit officer's own inspection is not a lesser
#: source of evidence than a registry lookup.
_SEVERITY = 45


def compute_godown_inspection_check(entry: dict | None) -> SignalResult:
    code = "LNC-15"
    if entry is None:
        return _unmeasurable(code, "no inspection finding recorded for this period")
    if not entry.get("finding"):
        return SignalResult(code, 0.0, None, "pass", 0,
                            "No godown inspection concerns recorded for this period.",
                            evidence_basis="manual")
    notes = str(entry.get("notes") or "").strip() or (
        "Godown inspection postponed for reasons the credit team found unconvincing.")
    return SignalResult(code, 1.0, None, band(_SEVERITY), _SEVERITY, notes,
                        evidence_basis="manual")


def compute_bill_verification_check(entry: dict | None) -> SignalResult:
    code = "LNC-16"
    if entry is None:
        return _unmeasurable(code, "no bill-verification finding recorded for this period")
    if not entry.get("finding"):
        return SignalResult(code, 0.0, None, "pass", 0,
                            "No bill-verification concerns recorded for this period.",
                            evidence_basis="manual")
    notes = str(entry.get("notes") or "").strip() or (
        "Original bills could not be produced for verification.")
    return SignalResult(code, 1.0, None, band(_SEVERITY), _SEVERITY, notes,
                        evidence_basis="manual")


def compute_invoice_compliance_check(entry: dict | None) -> SignalResult:
    code = "LNC-21"
    if entry is None:
        return _unmeasurable(code, "no invoice-compliance finding recorded for this period")
    if not entry.get("finding"):
        return SignalResult(code, 0.0, None, "pass", 0,
                            "Sampled invoices carried TAN and the other required "
                            "particulars for this period.", evidence_basis="manual")
    notes = str(entry.get("notes") or "").strip() or (
        "Sampled invoices were devoid of TAN or other required particulars.")
    return SignalResult(code, 1.0, None, band(_SEVERITY), _SEVERITY, notes,
                        evidence_basis="manual")
