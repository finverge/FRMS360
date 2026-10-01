"""LNC-02/LNC-22 - both measured against the same sanctioned project-appraisal baseline
(RBI 2016 illustrative signals #3 and #33). LNC-02 was built earlier in this catalogue
but always reported unmeasurable: no project-appraisal baseline existed anywhere in Lane
C to compare against. ``project_appraisals``/``project_progress`` (models.py) are that
baseline and its periodic update - the same two-point-comparison shape every ratio
signal here already uses, just fed by a direct submission (routes/lane_c.py) rather
than the extraction pipeline or a reference feed.

Both are level checks against the ORIGINAL sanctioned baseline from a single progress
submission, not a frequency count across several revisions over time - the same honest
scoping note LNC-13's "a level judgment, not a trend" already carries. RBI's own wording
("frequent change in scope") suggests a count across submissions; building that history
is a natural next step once more than one progress period exists for real borrowers, not
something fabricated now from a single data point.
"""
from __future__ import annotations

from datetime import date

from .signal_engine import SignalResult, _unmeasurable, band

#: A schedule slip beyond this many days past the sanctioned completion date is the
#: "frequent change in scope" RBI #3 names - a few weeks' slip is ordinary project
#: friction, several months is not.
_SCOPE_CREEP_DAYS = 90
#: Cost running above this fraction of the sanctioned figure is the "wide variance"
#: RBI #33 names.
_COST_VARIANCE_ABOVE = 1.2


def _baseline_and_progress(baseline: dict | None, progress: dict | None
                           ) -> tuple[dict, dict] | None:
    if baseline is None or progress is None:
        return None
    return baseline, progress


def compute_scope_creep(baseline: dict | None, progress: dict | None) -> SignalResult:
    code = "LNC-02"
    pair = _baseline_and_progress(baseline, progress)
    if pair is None:
        return _unmeasurable(code, "no project-appraisal baseline and progress "
                             "submission on file for this borrower/period")
    base, prog = pair
    revised: date | None = prog.get("revised_completion_date")
    sanctioned: date = base["sanctioned_completion_date"]
    if revised is None:
        return SignalResult(code, 0.0, None, "pass", 0,
                            "No revised completion date reported this period - project "
                            "tracking to the sanctioned schedule.", evidence_basis="ratio")

    slip_days = (revised - sanctioned).days
    if slip_days <= 30:
        return SignalResult(code, float(slip_days), 0.0, "pass", 0,
                            f"Completion date revised by {slip_days} day(s) - within "
                            f"ordinary project variance.", evidence_basis="ratio")

    severity = 35 if slip_days > _SCOPE_CREEP_DAYS else 15
    evidence = (f"Completion date revised from {sanctioned.isoformat()} to "
               f"{revised.isoformat()} - a {slip_days}-day slip against the sanctioned "
               f"schedule.")
    return SignalResult(code, float(slip_days), 0.0, band(severity), severity, evidence,
                        evidence_basis="ratio")


def compute_cost_variance(baseline: dict | None, progress: dict | None) -> SignalResult:
    code = "LNC-22"
    pair = _baseline_and_progress(baseline, progress)
    if pair is None:
        return _unmeasurable(code, "no project-appraisal baseline and progress "
                             "submission on file for this borrower/period")
    base, prog = pair
    sanctioned_cost = base["sanctioned_cost_paise"]
    actual_cost = prog["actual_cost_incurred_paise"]
    if not sanctioned_cost:
        return _unmeasurable(code, "sanctioned cost on the appraisal baseline is zero "
                             "or missing")

    ratio = actual_cost / sanctioned_cost
    if ratio <= _COST_VARIANCE_ABOVE:
        return SignalResult(code, ratio, 1.0, "pass", 0,
                            f"Cost incurred so far is {ratio:.0%} of the sanctioned "
                            f"project cost - within normal range.", evidence_basis="ratio")

    severity = 50 if ratio > 1.5 else 30
    evidence = (f"Cost incurred so far is {ratio:.0%} of the sanctioned project cost - "
               f"a wide variance against the appraised baseline.")
    return SignalResult(code, ratio, 1.0, band(severity), severity, evidence,
                        evidence_basis="ratio")
