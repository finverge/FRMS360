"""Graduation criteria before a rail moves from shadow to enforced (BR-316).

Lane A ships every rail in shadow mode by default (see decision_service's
``policy.STARTER``): the lane computes and records the decision it would have made and
always tells the caller to allow. That is a deliberate, safe default - a platform that
starts by declining a bank's payments does not get a second meeting - but it only stays
safe if leaving it is a recorded decision, not a config edit nobody had to justify.

This closes the narrower question OD-03 left open once "whether to sit in the
authorisation path" was decided (BRD, OD-03): not *whether* a rail may enforce, but what
a tenant must have observed, and who must approve, before it does.

**Why per rail, not once per tenant.** BR-104's board attestation covers a whole FRM
policy version in one shot because the policy is one board-approved document. A decision
policy is not: UPI might graduate on its own evidence months before RTGS does, and a
graduation record dated for one rail says nothing about another's false-positive rate.
So this validates every rail the submitted body marks enforced, independently, against
its own record - not the policy as a whole.

**Why a recorded figure, not a computed one - same reasoning as ``challenger.py``.** The
platform can compute how many decisions a rail *would have* stopped (decision_service's
``/decisions/{tenant}/shadow``); it cannot, from here, know how many of those were
correctly stopped rather than false alarms - that needs the disposition on the case that
either was or was not opened from it, which lives in a different service's schema by
design (``analytics-service has no grant on the decision schema, deliberately`` - see
``review.py``'s own docstring). So the false-positive rate is recorded, the same way a
board minute is: the platform refuses to proceed without one, but does not pretend to
re-derive it.
"""
from __future__ import annotations

from datetime import date, datetime, timezone

from . import attestation

#: A rail must have been in shadow mode at least this long before it may graduate.
#: Matches the platform's own 30-day baseline convention used elsewhere (VEL-01's
#: baseline window) - long enough to see a real month of traffic, not a quiet week.
MIN_OBSERVATION_DAYS = 30

REQUIRED = ("observation_window_days", "false_positive_rate", "evaluated_by",
           "evaluated_on", "approved_by", "approved_on", "reference")


class GraduationError(ValueError):
    """A rail is marked enforced without a graduation record that holds up. Never
    resolved by a default."""


def _enforced_rails(rails: dict) -> list[str]:
    """Rails this body would actually enforce inline - mode=inline and shadow=False.
    A near-real-time-only rail, or one still in shadow, needs no graduation record."""
    out = []
    for rail, spec in (rails or {}).items():
        if (str((spec or {}).get("mode", "")).lower() == "inline"
                and not bool((spec or {}).get("shadow", True))):
            out.append(str(rail).upper())
    return out


def validate(body: dict, *, now: datetime | None = None) -> dict:
    """Return the normalised graduation records, keyed by rail, or raise with what is
    wrong. Empty when no rail in this body is marked enforced - nothing to graduate."""
    rails = body.get("rails") or {}
    enforced = _enforced_rails(rails)
    if not enforced:
        return {}

    graduation_in = {str(k).upper(): v for k, v in (body.get("graduation") or {}).items()}
    missing_rails = [r for r in enforced if r not in graduation_in]
    if missing_rails:
        raise GraduationError(
            "These rails are marked enforced but carry no graduation record: "
            + ", ".join(missing_rails) + ". A rail does not leave shadow mode without "
            "one (BR-316) - minimum observation window, observed false-positive rate, "
            "and board/ACB sign-off.")

    today = (now or datetime.now(timezone.utc)).date()
    out: dict[str, dict] = {}
    for rail in enforced:
        rec = graduation_in[rail] or {}
        missing = [f for f in REQUIRED if not str(rec.get(f) if rec.get(f) is not None
                                                    else "").strip()]
        if missing:
            raise GraduationError(
                f"{rail}'s graduation record is incomplete: {', '.join(missing)} "
                "missing.")

        try:
            window_days = int(rec["observation_window_days"])
        except (TypeError, ValueError) as exc:
            raise GraduationError(
                f"{rail}: observation_window_days must be a whole number of days"
            ) from exc
        if window_days < MIN_OBSERVATION_DAYS:
            raise GraduationError(
                f"{rail}: {window_days} day(s) of shadow-mode observation is below "
                f"the {MIN_OBSERVATION_DAYS}-day minimum. Enforcement never turns on "
                "by a missed validation - only by an explicit, recorded decision.")

        try:
            fp_rate = float(rec["false_positive_rate"])
        except (TypeError, ValueError) as exc:
            raise GraduationError(
                f"{rail}: false_positive_rate must be a number") from exc
        if not (0.0 <= fp_rate <= 1.0):
            raise GraduationError(
                f"{rail}: false_positive_rate {fp_rate} must be between 0 and 1 - it "
                "is a rate, not a percentage or a count.")

        evaluated_on = _date_field(rail, rec, "evaluated_on", today)
        approved_on = _date_field(rail, rec, "approved_on", today)

        body_key = str(rec["approved_by"]).strip().lower()
        if body_key not in attestation.APPROVING_BODIES:
            raise GraduationError(
                f"{rail}: '{rec['approved_by']}' is not a recognised approving body. "
                f"One of: {', '.join(sorted(attestation.APPROVING_BODIES))}.")

        reference = str(rec["reference"]).strip()
        if len(reference) < 3:
            raise GraduationError(
                f"{rail}: reference is too short to identify a minute or resolution - "
                "the pointer an inspector follows from the decision back to the board.")

        out[rail] = {
            "observation_window_days": window_days,
            "false_positive_rate": round(fp_rate, 4),
            "evaluated_by": str(rec["evaluated_by"]).strip(),
            "evaluated_on": evaluated_on.isoformat(),
            "approved_by": body_key,
            "approved_by_label": attestation.APPROVING_BODIES[body_key],
            "approved_on": approved_on.isoformat(),
            "reference": reference[:200],
        }
    return out


def _date_field(rail: str, rec: dict, key: str, today: date) -> date:
    raw = str(rec[key]).strip()
    try:
        value = date.fromisoformat(raw[:10])
    except ValueError as exc:
        raise GraduationError(
            f"{rail}: {key} '{raw}' is not a date (expected YYYY-MM-DD).") from exc
    if value > today:
        raise GraduationError(
            f"{rail}: {key} {value} is in the future. A rail cannot have been "
            "observed, or approved, before today.")
    return value


def requires_graduation(kind: str) -> bool:
    """Only the decision_policy kind - see the module docstring."""
    return (kind or "").strip().lower() == "decision_policy"
