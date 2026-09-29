"""Board approval for a policy version (BR-104), and committee approval for a model
version (BR-808).

The Directions do not merely require a bank to *have* fraud-risk thresholds — they
require those thresholds to be the ones its board approved. A platform that lets any
tenant administrator activate a new SLA window or LEA referral floor silently converts a
board-approved policy into an operator preference, and the difference only surfaces when
an inspector asks who authorised the number. FREE-AI asks the same question of a model:
who in the model risk governance structure signed off before it went live.

So activation of a ``policy`` or ``model`` config is gated: it needs the approving body,
the date it was approved, and the minute or resolution reference. Those are recorded on
the version itself, immutably, so the question "under whose authority was this in force
on 14 March" is answerable from the row rather than from somebody's memory.

**Why a refusal rather than an optional field.** An optional attestation is one nobody
fills in, and a blank field is indistinguishable from an unapproved policy. The refusal
is also narrow on purpose: it applies to ``policy`` and ``model`` configs only. Rule
thresholds are tuned continuously by the fraud team and gating every retune behind a
board minute would push that work off-platform, which is the outcome the lateness rule
elsewhere exists to avoid.
"""
from __future__ import annotations

from datetime import date, datetime, timezone

#: Who can approve a fraud risk policy, or a model version. Entity type decides which of
#: the policy bodies is correct — a UCB has a Board of Management where a commercial bank
#: has a Special Committee. model_risk_committee is FREE-AI's own governance body and
#: applies regardless of entity type.
APPROVING_BODIES = {
    "board": "Board of Directors",
    "acb": "Audit Committee of the Board",
    "board_of_management": "Board of Management (UCB)",
    "scbmf": "Special Committee of the Board for Monitoring Frauds",
    "risk_committee": "Risk Management Committee",
    "model_risk_committee": "Model Risk Management Committee",
}

REQUIRED = ("approved_by", "approved_on", "reference")


class AttestationError(ValueError):
    """The attestation is missing or unusable. Never resolved by a default."""


def validate(body: dict, *, now: datetime | None = None) -> dict:
    """Return the normalised attestation, or raise with what is wrong.

    Every failure names the field and why it matters, because "invalid attestation" tells
    an administrator nothing they can act on.
    """
    att = (body or {}).get("attestation")
    if not isinstance(att, dict) or not att:
        raise AttestationError(
            "This policy version has no board approval recorded. A fraud risk policy is "
            "the board's, not the operator's — activating one without saying who approved "
            "it makes the thresholds unattributable. Supply attestation with "
            f"{', '.join(REQUIRED)}.")

    missing = [f for f in REQUIRED if not str(att.get(f) or "").strip()]
    if missing:
        raise AttestationError(
            f"The board approval is incomplete: {', '.join(missing)} missing. "
            "An inspection asks who approved a threshold, when, and against which minute.")

    body_key = str(att["approved_by"]).strip().lower()
    if body_key not in APPROVING_BODIES:
        raise AttestationError(
            f"'{att['approved_by']}' is not a recognised approving body. One of: "
            f"{', '.join(sorted(APPROVING_BODIES))}.")

    raw = str(att["approved_on"]).strip()
    try:
        approved_on = date.fromisoformat(raw[:10])
    except ValueError as exc:
        raise AttestationError(
            f"approved_on '{raw}' is not a date (expected YYYY-MM-DD).") from exc

    today = (now or datetime.now(timezone.utc)).date()
    if approved_on > today:
        raise AttestationError(
            f"approved_on {approved_on} is in the future. A policy cannot be in force "
            "under an approval that has not happened.")

    reference = str(att["reference"]).strip()
    if len(reference) < 3:
        raise AttestationError(
            "reference is too short to identify a minute or resolution. This is the "
            "pointer an inspector follows from the threshold back to the decision.")

    return {
        "approved_by": body_key,
        "approved_by_label": APPROVING_BODIES[body_key],
        "approved_on": approved_on.isoformat(),
        "reference": reference[:200],
        "note": str(att.get("note") or "").strip()[:500],
    }


def requires_attestation(kind: str) -> bool:
    """The FRM policy (BR-104) and a model version (BR-808). Rule retuning stays
    unblocked — see the module docstring."""
    return (kind or "").strip().lower() in ("policy", "model")
