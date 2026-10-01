"""LNC-23 - resignation of key managerial personnel, or frequent change in management
(RBI 2016 illustrative signal #42). Same reference-feed shape as roc_mca_check.py's
LNC-10, but the finding is a count rather than a boolean: one resignation in a year is
unremarkable, "frequent" change is what the signal names.

**Feed contract** for the ``management_changes`` kind's entry attributes:
``{"changes_12m": int, "detail": str}`` - the count of director/KMP changes MCA's own
filing history shows for this borrower in the trailing 12 months, as the feed computes
it; Lane C does not recompute this from raw filings itself.
"""
from __future__ import annotations

from .signal_engine import SignalResult, _unmeasurable, band

#: One change in a year is ordinary business. Two is the "frequent" RBI #42 names;
#: three or more compounds toward critical via band()'s own thresholds.
_PER_CHANGE_SEVERITY = 20
_FREQUENT_FROM = 2


def compute_management_change_check(company_identifier: str | None,
                                    entry: dict | None) -> SignalResult:
    code = "LNC-23"
    if not company_identifier:
        return _unmeasurable(code, "no CIN/PAN on file for this borrower")
    if entry is None:
        return _unmeasurable(code, "no active management_changes feed loaded, or this "
                             "borrower is not in the loaded list")

    attrs = entry.get("attributes") or {}
    changes = attrs.get("changes_12m")
    if not isinstance(changes, (int, float)) or changes < 0:
        return _unmeasurable(code, "feed entry has no valid changes_12m")
    changes = int(changes)

    if changes < _FREQUENT_FROM:
        return SignalResult(code, float(changes), None, "pass", 0,
                            f"{changes} director/KMP change(s) in the trailing 12 "
                            f"months - not frequent enough to flag.",
                            evidence_basis="mca_roc feed")

    severity = changes * _PER_CHANGE_SEVERITY
    detail = str(attrs.get("detail", ""))[:300]
    evidence = (f"{changes} director/KMP changes in the trailing 12 months."
               + (f" {detail}" if detail else ""))
    return SignalResult(code, float(changes), None, band(severity), severity, evidence,
                        evidence_basis="mca_roc feed")
