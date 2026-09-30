"""Aggregate LNC signal results into a single 0-100 credit health score.

Starts at 100, deducts per fired signal by its band (``signal_engine.band()``), floors at
0. ``unmeasurable`` signals are excluded from both the score and ``signal_count`` -
excluded, not scored as clean, the same reporting discipline the rest of the platform
applies to a rule with no data feed.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from ..signals.signal_engine import SignalResult

#: Points deducted per band. Applied once per fired signal (pass and unmeasurable
#: deduct nothing).
_DEDUCTION = {"critical": 20, "high": 10, "medium": 5, "low": 2, "pass": 0}


@dataclass
class ScoreResult:
    score_value: int
    trend: str
    signal_count: int
    critical_count: int
    recommendation: str
    band_counts: dict[str, int] = field(default_factory=dict)


def _recommendation(score: int) -> str:
    if score >= 80:
        return "continue"
    if score >= 60:
        return "monitor"
    if score >= 40:
        return "investigate"
    return "escalate"


def _trend(score: int, prior_score: int | None) -> str:
    if prior_score is None:
        return "new"
    if score < prior_score - 5:
        return "deteriorating"
    if score > prior_score + 5:
        return "improving"
    return "stable"


def score(signals: dict[str, SignalResult], prior_score: int | None = None) -> ScoreResult:
    measured = [s for s in signals.values() if s.status != "unmeasurable"]
    fired = [s for s in measured if s.status != "pass"]

    band_counts: dict[str, int] = {}
    total = 100
    for s in fired:
        total -= _DEDUCTION.get(s.status, 0)
        band_counts[s.status] = band_counts.get(s.status, 0) + 1
    total = max(0, total)

    return ScoreResult(
        score_value=total,
        trend=_trend(total, prior_score),
        signal_count=len(fired),
        critical_count=band_counts.get("critical", 0),
        recommendation=_recommendation(total),
        band_counts=band_counts,
    )
