"""LNC-20 - a raid or enforcement action by Income Tax/GST/TDS/central excise
authorities (RBI 2016 illustrative signal #40).

Different truth shape from every other reference-feed signal in this catalogue: for
mca_roc/stock_audit, presence of a matching entry only means "this borrower is covered by
the feed" - a boolean sub-field says whether the actual red flag is present. Here,
presence of the entry itself IS the finding: an enforcement-action feed has no reason to
publish a row for a borrower unless an action occurred, so there is no separate
"has_finding" field to check.

**Feed contract**: ``{"action_type": str, "authority": str, "date": str}``.
"""
from __future__ import annotations

from .signal_engine import SignalResult, _unmeasurable, band

_SEVERITY = 50


def compute_enforcement_action_check(company_identifier: str | None,
                                     entry: dict | None,
                                     feed_loaded: bool) -> SignalResult:
    code = "LNC-20"
    if not company_identifier:
        return _unmeasurable(code, "no CIN/PAN on file for this borrower")
    if not feed_loaded:
        return _unmeasurable(code, "no active enforcement_action feed loaded for this tenant")
    if entry is None:
        return SignalResult(code, 0.0, None, "pass", 0,
                            "No enforcement action on file for this borrower.",
                            evidence_basis="enforcement")

    attrs = entry.get("attributes") or {}
    authority = str(attrs.get("authority", "")) or "an enforcement authority"
    action_type = str(attrs.get("action_type", "action"))
    when = str(attrs.get("date", ""))
    evidence = f"{authority} recorded a {action_type}" + (f" on {when}." if when else ".")
    return SignalResult(code, 1.0, None, band(_SEVERITY), _SEVERITY, evidence,
                        evidence_basis="enforcement")
