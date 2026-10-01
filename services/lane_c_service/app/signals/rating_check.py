"""LNC-11 — a negative rating action (downgrade, or outlook turned negative) since the
borrower's last Lane C review. Not an RBI 2016 illustrative signal - added value beyond
the checklist, and should be presented to customers that way, not counted toward RBI
coverage.

Same pure-function shape as roc_mca_check.py: the caller looks up the active
``rating_action`` feed entry and the prior period's recorded rank, and passes both in.

**Feed contract** for the ``rating_action`` kind's entry attributes:
``{"rank": int (1=best/AAA-equivalent .. 8=worst/D-equivalent, agency-agnostic so this
works the same whether the feed is CRISIL, ICRA, CARE or India Ratings), "grade": str
(the agency's own label, for display), "outlook": "positive"|"stable"|"negative",
"agency": str}``. A missing or out-of-range ``rank`` is treated as a malformed feed row.

**Why outlook alone can fire with no prior rank on file, but a downgrade can't**:
outlook is a single point-in-time fact - "is this rating currently on negative
watch" needs no history to answer. A downgrade is inherently a comparison, so it can
only fire once Lane C has recorded a rank for this borrower at least once before; until
then that half is honestly nothing to report, not unmeasurable - the platform DOES have
today's rating, it just has nothing yet to compare it against.
"""
from __future__ import annotations

from .signal_engine import SignalResult, _unmeasurable, band

_VALID_OUTLOOKS = {"positive", "stable", "negative"}


def compute_rating_check(company_identifier: str | None, cur_rating_entry: dict | None,
                          prior_rank: float | None) -> SignalResult:
    code = "LNC-11"
    if not company_identifier:
        return _unmeasurable(code, "no CIN/PAN on file for this borrower")
    if cur_rating_entry is None:
        return _unmeasurable(code, "no active rating feed loaded, or this borrower is "
                             "not in the loaded list")

    attrs = cur_rating_entry.get("attributes") or {}
    rank = attrs.get("rank")
    outlook = attrs.get("outlook")
    if not isinstance(rank, (int, float)) or not (1 <= rank <= 8):
        return _unmeasurable(code, "feed entry has no valid rank (expected 1-8)")
    if outlook not in _VALID_OUTLOOKS:
        return _unmeasurable(code, "feed entry has no valid outlook")

    grade = str(attrs.get("grade", ""))
    agency = str(attrs.get("agency", ""))

    severity = 0
    reasons = []
    if outlook == "negative":
        severity += 25
        reasons.append(f"{agency or 'rating agency'} outlook is negative")
    if prior_rank is not None and rank > prior_rank:
        severity += 30
        reasons.append(f"rank moved from {prior_rank:.0f} to {rank:.0f} (worse)")

    if not reasons:
        detail = f" ({grade}, {agency})" if grade else ""
        return SignalResult(code, rank, prior_rank, "pass", 0,
                            f"No negative rating action on file{detail}.",
                            evidence_basis="rating feed")

    evidence = f"{'; '.join(reasons)}{f' ({grade})' if grade else ''}."
    return SignalResult(code, rank, prior_rank, band(severity), severity, evidence,
                        evidence_basis="rating feed")
