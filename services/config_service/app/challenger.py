"""Champion/challenger evaluation before a model configuration is promoted (BR-807).

FREE-AI expects a candidate model to be compared against the one it would replace before
it goes live, not approved on its own numbers in isolation. This is gated at the same
point BR-104/BR-808 gate board and committee attestation - refused at proposal, not
silently defaulted, because an empty evaluation record is indistinguishable from a
challenger nobody actually tested.

**Deliberately narrow, same reasoning as attestation.py.** This applies to the ``model``
kind only. This gate was built ahead of any model existing - see the AI/ML Roadmap and
OD-06 - specifically so it would exist on day one rather than be retrofitted under
delivery pressure once one was proposed. AI/ML roadmap Phase 2 (transaction
velocity/anomaly detection) now scores real traffic through it (VEL-04); Phases 3 and 4
have no live scoring yet.

**Why a recorded evaluation rather than a computed one.** BR-806's backtesting harness is
how a candidate is actually run against history; this module does not re-run it. It
validates that the *result* of doing so - what was measured, against what champion, by
whom - was written down, the same relationship attestation.py has to a board minute: the
platform cannot re-derive that a board meeting happened, only refuse to proceed without a
record that it did.
"""
from __future__ import annotations

from datetime import date, datetime, timezone

REQUIRED = ("evaluated_by", "evaluated_on", "method", "metrics", "comparison_summary")


class ChallengerEvaluationError(ValueError):
    """The champion/challenger evaluation is missing or unusable. Never resolved by a
    default."""


def validate(body: dict, *, now: datetime | None = None) -> dict:
    """Return the normalised evaluation, or raise with what is wrong.

    Every failure names the field and why it matters, same convention as
    ``attestation.validate`` - "invalid evaluation" tells a data scientist nothing they
    can act on.
    """
    ev = (body or {}).get("challenger_evaluation")
    if not isinstance(ev, dict) or not ev:
        raise ChallengerEvaluationError(
            "This model version has no champion/challenger evaluation recorded. A "
            "candidate is compared against the model it would replace before it is even "
            "proposed for activation, not approved on its own numbers alone. Supply "
            f"challenger_evaluation with {', '.join(REQUIRED)}.")

    def _blank(field: str) -> bool:
        val = ev.get(field)
        return not val if field == "metrics" else not str(val or "").strip()

    missing = [f for f in REQUIRED if _blank(f)]
    if missing:
        raise ChallengerEvaluationError(
            f"The champion/challenger evaluation is incomplete: {', '.join(missing)} "
            "missing. An inspection asks what was measured, against what champion, and "
            "by whom.")

    metrics = ev.get("metrics")
    if not isinstance(metrics, dict) or not metrics:
        raise ChallengerEvaluationError(
            "At least one measured metric is required (e.g. precision, recall, "
            "false-positive rate) - a comparison with no numbers is an assertion, not "
            "an evaluation.")
    try:
        metrics_out = {str(k): float(v) for k, v in metrics.items()}
    except (TypeError, ValueError) as exc:
        raise ChallengerEvaluationError(
            "Every metric value must be a number.") from exc

    raw = str(ev["evaluated_on"]).strip()
    try:
        evaluated_on = date.fromisoformat(raw[:10])
    except ValueError as exc:
        raise ChallengerEvaluationError(
            f"evaluated_on '{raw}' is not a date (expected YYYY-MM-DD).") from exc

    today = (now or datetime.now(timezone.utc)).date()
    if evaluated_on > today:
        raise ChallengerEvaluationError(
            f"evaluated_on {evaluated_on} is in the future. An evaluation cannot have "
            "been run before it happened.")

    method = str(ev["method"]).strip()
    if len(method) < 8:
        raise ChallengerEvaluationError(
            "method is too short to describe how the comparison was actually run "
            "(e.g. which backtest window, what data).")

    summary = str(ev["comparison_summary"]).strip()
    if len(summary) < 10:
        raise ChallengerEvaluationError(
            "comparison_summary is too short to say how this challenger compares to "
            "its champion - or, for a first model version, that there was no champion "
            "to compare against.")

    return {
        "champion_version": str(ev.get("champion_version") or "").strip(),
        "evaluated_by": str(ev["evaluated_by"]).strip(),
        "evaluated_on": evaluated_on.isoformat(),
        "method": method[:500],
        "metrics": metrics_out,
        "comparison_summary": summary[:1000],
    }


def requires_evaluation(kind: str) -> bool:
    """Only the model kind — see the module docstring."""
    return (kind or "").strip().lower() == "model"
