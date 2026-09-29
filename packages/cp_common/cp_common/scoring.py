"""How fired rules become a score, and a score becomes a severity.

Shared for the same reason ``observations.observe`` is shared: severity decides whether a
case is opened, whether a board pack mentions it, and — since Lane A — whether a customer's
payment is declined. Two lanes with two implementations of "critical" would eventually
disagree about the same account, and there would be no way to say which was right.

Severity is a property of the **alert**, not of a rule. A single velocity hit is not
critical; velocity plus layering plus structuring on one payment is. That is why the score
sums family weights across everything that fired rather than reading a severity off each
rule — which is what Lane A originally tried to do, against a field the catalogue does not
carry, so every match came back ``unstated`` and the decline floor could never be met.
"""
from __future__ import annotations

#: Weight per rule family. Layering and structuring outrank a lone velocity hit because
#: they are harder to explain innocently, not because they are rarer. LOS (BR-214) sits
#: with SME: a falsified application, a straw borrower or a collusive valuation is
#: deliberate concealment at the point of origination, not a noisy customer.
FAMILY_WEIGHT = {"LAY": 140, "SME": 130, "CPT": 120, "VEL": 100, "BEH": 100,
                 "CHN": 90, "TBM": 120, "CBS": 110, "QUAL": 60, "LOS": 130}

#: Used when a family carries no weight of its own. Deliberately mid-range: a new family
#: should register, not be silently ignored.
DEFAULT_WEIGHT = 100

#: Fallbacks when the tenant's policy does not state a band. These match the platform
#: defaults the detection engine has always used.
DEFAULT_BANDS = {"critical": 380, "high": 260, "medium": 150}

SEVERITY_ORDER = {"low": 0, "medium": 1, "high": 2, "critical": 3}


def score_of(families) -> float:
    """Total score for the families that fired on one transaction."""
    return float(sum(FAMILY_WEIGHT.get(f or "", DEFAULT_WEIGHT) for f in families))


def severity_for(score: float, policy: dict | None = None) -> str:
    """The tenant's own bands decide. This owns only the comparison."""
    p = policy or {}
    if score >= p.get("severity_critical_score", DEFAULT_BANDS["critical"]):
        return "critical"
    if score >= p.get("severity_high_score", DEFAULT_BANDS["high"]):
        return "high"
    if score >= p.get("severity_medium_score", DEFAULT_BANDS["medium"]):
        return "medium"
    return "low"


def at_least(severity: str, floor: str) -> bool:
    """Is ``severity`` at or above ``floor``? An unstated floor never escalates."""
    if not floor:
        return False
    return (SEVERITY_ORDER.get((severity or "").lower(), -1)
            >= SEVERITY_ORDER.get(floor.lower(), 99))
