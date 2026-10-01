"""LNC-18 - a critical issue highlighted in a third-party stock audit report (RBI 2016
illustrative signal #17). Identical shape to roc_mca_check.py's LNC-10 - a boolean
finding plus detail, read from the ``stock_audit`` reference feed.

**Feed contract**: ``{"has_critical_issue": bool, "detail": str}``.
"""
from __future__ import annotations

from .signal_engine import SignalResult, _unmeasurable, band

_SEVERITY = 45


def compute_stock_audit_check(company_identifier: str | None,
                              stock_audit_entry: dict | None) -> SignalResult:
    code = "LNC-18"
    if not company_identifier:
        return _unmeasurable(code, "no CIN/PAN on file for this borrower")
    if stock_audit_entry is None:
        return _unmeasurable(code, "no active stock_audit feed loaded, or this borrower "
                             "is not in the loaded list")

    attrs = stock_audit_entry.get("attributes") or {}
    if "has_critical_issue" not in attrs:
        return _unmeasurable(code, "feed entry is missing has_critical_issue")

    if not attrs["has_critical_issue"]:
        return SignalResult(code, 0.0, None, "pass", 0,
                            "No critical stock-audit issues on file for this borrower.",
                            evidence_basis="stock feed")

    detail = str(attrs.get("detail", ""))[:300]
    evidence = f"Stock audit report flags a critical issue. {detail}".strip()
    return SignalResult(code, 1.0, None, band(_SEVERITY), _SEVERITY, evidence,
                        evidence_basis="stock feed")
