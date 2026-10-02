"""LNC-02/LNC-22 - both measured against the same sanctioned project-appraisal baseline
(RBI 2016 illustrative signals #3 and #33). LNC-02 was built earlier in this catalogue
but always reported unmeasurable: no project-appraisal baseline existed anywhere in Lane
C to compare against. ``project_appraisals``/``project_progress`` (models.py) are that
baseline and its periodic update - the same two-point-comparison shape every ratio
signal here already uses, just fed by a direct submission (routes/lane_c.py) rather
than the extraction pipeline or a reference feed.

LNC-22 is a level check against the ORIGINAL sanctioned baseline from a single progress
submission, not a frequency count - the same honest scoping note LNC-13's "a level
judgment, not a trend" already carries. LNC-02 is not: it also counts how many times a
borrower's reported completion date has actually changed across every period on file,
compounding with its own single-period magnitude check - RBI's own word "frequent"
names a count over time, which a single submission can never answer on its own.
"""
from __future__ import annotations

from datetime import date

from .signal_engine import SignalResult, _unmeasurable, band

#: A schedule slip beyond this many days past the sanctioned completion date is itself
#: a red flag - a few weeks' slip is ordinary project friction, several months is not.
_SCOPE_CREEP_DAYS = 90
#: Cost running above this fraction of the sanctioned figure is the "wide variance"
#: RBI #33 names.
_COST_VARIANCE_ABOVE = 1.2
#: Two distinct changes to the reported completion date, across however many periods
#: on file, is the "frequent" RBI #3 names on its own - a single revision is ordinary;
#: three or more compounds further. Independent of, and additive with, the magnitude
#: check above: a project can slip once by a lot, or slip by a little several times,
#: and RBI's wording is squarely about the second shape.
_FREQUENT_FROM = 2


def _baseline_and_progress(baseline: dict | None, progress: dict | None
                           ) -> tuple[dict, dict] | None:
    if baseline is None or progress is None:
        return None
    return baseline, progress


def _count_revisions(sanctioned: date, progress_history: list[dict] | None) -> int:
    """How many times the reported completion date has actually changed, walking every
    period on file in order - not whether this one period's figure is big, but whether
    the figure keeps moving. Starts from the sanctioned baseline; each period whose
    ``revised_completion_date`` differs from the running "effective" date is one
    revision, and becomes the new effective date for the next period's comparison."""
    if not progress_history:
        return 0
    count = 0
    effective = sanctioned
    for row in sorted(progress_history, key=lambda r: r["reporting_date"]):
        revised = row.get("revised_completion_date")
        if revised is not None and revised != effective:
            count += 1
            effective = revised
    return count


def compute_scope_creep(baseline: dict | None, progress: dict | None,
                        progress_history: list[dict] | None = None) -> SignalResult:
    code = "LNC-02"
    pair = _baseline_and_progress(baseline, progress)
    if pair is None:
        return _unmeasurable(code, "no project-appraisal baseline and progress "
                             "submission on file for this borrower/period")
    base, prog = pair
    sanctioned: date = base["sanctioned_completion_date"]
    revised: date | None = prog.get("revised_completion_date")

    magnitude_severity = 0
    slip_days = 0
    if revised is not None:
        slip_days = (revised - sanctioned).days
        if slip_days > 30:
            magnitude_severity = 35 if slip_days > _SCOPE_CREEP_DAYS else 15

    revision_count = _count_revisions(sanctioned, progress_history)
    revision_severity = 0
    if revision_count >= _FREQUENT_FROM:
        revision_severity = 30 if revision_count >= 3 else 15

    severity = magnitude_severity + revision_severity
    if severity == 0:
        evidence = (f"Completion date revised by {slip_days} day(s) - within ordinary "
                   f"project variance." if revised is not None else
                   "No revised completion date reported this period - project "
                   "tracking to the sanctioned schedule.")
        return SignalResult(code, float(slip_days), 0.0, "pass", 0, evidence,
                            evidence_basis="ratio")

    reasons = []
    if magnitude_severity:
        reasons.append(f"completion date revised from {sanctioned.isoformat()} to "
                       f"{revised.isoformat()} - a {slip_days}-day slip against the "
                       f"sanctioned schedule")
    if revision_severity:
        reasons.append(f"the completion date has changed {revision_count} times "
                       f"across reporting periods - a frequent change in scope")
    evidence = "; ".join(reasons).capitalize() + "."
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
