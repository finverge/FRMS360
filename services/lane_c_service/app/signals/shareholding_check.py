"""LNC-19 - reduction in promoter stake, or an increase in encumbered shares (RBI 2016
illustrative signal #41). Same before/after shape as rating_check.py's LNC-11: current
encumbrance is a single-point-in-time fact that can fire on its own, a stake *reduction*
is inherently a comparison and needs a prior figure on file.

**Feed contract** for the ``shareholding`` kind's entry attributes:
``{"promoter_stake_pct": float, "encumbered_pct": float}`` (both 0-100).
"""
from __future__ import annotations

from .signal_engine import SignalResult, _unmeasurable, band

#: Encumbered shares above this fraction of the promoter's holding is itself a warning,
#: with no history needed to say so.
_ENCUMBERED_ABOVE_PCT = 50.0
#: A promoter-stake drop of at least this many percentage points since the last review
#: is the reduction RBI's wording names - a rounding-level wobble is not.
_STAKE_DROP_PP = 5.0


def compute_shareholding_check(company_identifier: str | None,
                               cur_entry: dict | None,
                               prior_promoter_pct: float | None) -> SignalResult:
    code = "LNC-19"
    if not company_identifier:
        return _unmeasurable(code, "no CIN/PAN on file for this borrower")
    if cur_entry is None:
        return _unmeasurable(code, "no active shareholding feed loaded, or this "
                             "borrower is not in the loaded list")

    attrs = cur_entry.get("attributes") or {}
    promoter_pct = attrs.get("promoter_stake_pct")
    encumbered_pct = attrs.get("encumbered_pct")
    if not isinstance(promoter_pct, (int, float)) or not isinstance(encumbered_pct, (int, float)):
        return _unmeasurable(code, "feed entry has no valid promoter_stake_pct/encumbered_pct")

    severity = 0
    reasons = []
    if encumbered_pct > _ENCUMBERED_ABOVE_PCT:
        severity += 30
        reasons.append(f"{encumbered_pct:.0f}% of promoter shares are encumbered")
    if prior_promoter_pct is not None and (prior_promoter_pct - promoter_pct) >= _STAKE_DROP_PP:
        severity += 30
        reasons.append(f"promoter stake fell from {prior_promoter_pct:.1f}% to "
                       f"{promoter_pct:.1f}% since the last review")

    if not reasons:
        return SignalResult(code, promoter_pct, prior_promoter_pct, "pass", 0,
                            f"Promoter stake {promoter_pct:.1f}%, "
                            f"{encumbered_pct:.0f}% encumbered - no concern on file.",
                            evidence_basis="shareholding")

    evidence = "; ".join(reasons) + "."
    return SignalResult(code, promoter_pct, prior_promoter_pct, band(severity), severity,
                        evidence, evidence_basis="shareholding")
