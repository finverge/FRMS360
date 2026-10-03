"""The RFA case lifecycle as an enforced state machine.

Until now a case's ``state`` was a column anything could write. That is not good enough for
this domain: RBI's Master Directions on Fraud Risk Management are largely about *what a
bank does after an early-warning signal trips*, and the Supreme Court in **State Bank of
India v. Rajesh Agarwal (2023)** held that classifying a borrower as fraudulent without
giving them a hearing violates natural justice. Both of those are procedural guarantees,
and a procedural guarantee that lives in a free-text column is not a guarantee.

So the rules live here, in one place, and every state change goes through them:

* **You cannot skip the hearing.** Moving a case out of ``natural_justice`` before the
  borrower's response window has actually elapsed is refused. The window length is the
  tenant's own board-approved figure, not a constant - a Tier-1 UCB and a large commercial
  bank are governed by different directions.
* **You cannot declare fraud without a reasoned order.** ``declare_fraud`` is refused
  unless a ``reasoned_order`` document is on the case. This is the same condition the
  ``fraud_without_reasoned_order`` metric reports on; now it cannot arise in the first
  place.
* **You cannot declare fraud alone.** It needs a maker and a separate checker. The
  proposer is never allowed to be their own approver.
* **Late is recorded, not blocked.** Issuing a show-cause notice after the policy window
  is still better than never issuing one, so it is allowed - and permanently marked as a
  breach on the transition record. Silently permitting it, or silently refusing it, would
  both hide the fact from an inspection.

Every attempt - allowed or refused - is written to ``case_transitions``.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from cp_common.permissions import case_action

# Who may act as the *checker* half of a maker-checker pair is the "case.approve_transition"
# grant on the tenant's role rows, resolved by the caller into ``Context.may_approve``.


@dataclass(frozen=True)
class Transition:
    action: str
    src: str
    dst: str
    label: str
    # Free-text justification the actor must supply.
    requires_reason: bool = False
    # A document of this type must already be attached to the case.
    requires_document: str = ""
    # Needs a second, different person holding the approve permission.
    requires_second_approval: bool = False
    # Refuse while the borrower's response window is still open.
    requires_window_elapsed: bool = False
    # Refuse until the staff accountability examination has been concluded (BR-414).
    requires_accountability: bool = False
    # Record (do not block) when this ran later than the named policy window.
    late_after_days: str = ""
    # Policy keys whose clocks this transition starts.
    starts_clocks: tuple[str, ...] = field(default_factory=tuple)


def _t(*args, **kwargs) -> Transition:
    """Who may initiate a transition is not stated here: it is the ``case.act.<action>``
    permission on the tenant's own role rows (cp_common.permissions), so an administrator
    changes it in the console rather than in code."""
    return Transition(*args, **kwargs)


TRANSITIONS: tuple[Transition, ...] = (
    # ---- triage -------------------------------------------------------------
    _t("flag_rfa", "under_review", "rfa_flagged",
       "Flag as Red Flagged Account",
       requires_reason=True),
    _t("close_no_fraud", "under_review", "exonerated",
       "Close - not fraud",
       requires_reason=True),

    # ---- natural justice ----------------------------------------------------
    _t("revoke_rfa", "rfa_flagged", "under_review",
       "Revoke the RFA flag",
       requires_reason=True),
    # The show-cause notice opens the hearing. RBI expects it promptly after the RFA
    # flag; issuing it late is recorded against the case rather than silently allowed.
    _t("issue_show_cause", "rfa_flagged", "natural_justice",
       "Issue show-cause notice",
       requires_reason=False,
       late_after_days="show_cause_within_days",
       starts_clocks=("natural_justice_days",)),
    # The borrower replied. Always permitted - a reply can arrive at any time.
    _t("record_response", "natural_justice", "response_evaluation",
       "Record the borrower's response",
       requires_reason=True),
    # No reply came. Only once the window has genuinely closed.
    _t("close_window", "natural_justice", "response_evaluation",
       "Close the response window (no reply received)",
       requires_window_elapsed=True),

    # ---- decision -----------------------------------------------------------
    # The adverse finding. A hearing was held, so the decision must be a speaking order,
    # and one person may not both propose and approve it.
    _t("declare_fraud", "response_evaluation", "fraud_declared",
       "Declare fraud",
       requires_reason=True,
       requires_document="reasoned_order",
       requires_second_approval=True,
       starts_clocks=("fmr_filing_days", "str_filing_days",
                      "staff_accountability_days")),
    _t("exonerate", "response_evaluation", "exonerated",
       "Exonerate - allegation not sustained",
       requires_reason=True),

    # ---- regulatory reporting ----------------------------------------------
    # Deliberately carries no accountability guard. RBI is explicit that reporting must
    # not wait for the staff accountability exercise, and blocking the return until staff
    # had been examined would turn a governance requirement into a reporting delay.
    _t("file_fmr", "fraud_declared", "fmr_reported",
       "File the Fraud Monitoring Return",
       late_after_days="fmr_filing_days"),
    # Closing is where the question has to be answered. A fraud may be *reported* with
    # accountability still open; it may not be filed away with it never examined, which is
    # precisely the paper-compliance gap an inspection looks for.
    _t("close_case", "fmr_reported", "closed_fraud",
       "Close the case",
       requires_accountability=True),

    # ---- reopening ----------------------------------------------------------
    # Exoneration is not always final; new evidence appears. Reopening a *closed fraud*
    # is deliberately not offered - that record has been reported to RBI.
    _t("reopen", "exonerated", "under_review",
       "Reopen on new evidence",
       requires_reason=True),
)

BY_ACTION = {t.action: t for t in TRANSITIONS}
TERMINAL = ("closed_fraud",)


def available(state: str) -> list[Transition]:
    return [t for t in TRANSITIONS if t.src == state]


class TransitionRefused(Exception):
    """A transition that the lifecycle does not permit, with the reason why."""

    def __init__(self, message: str, code: str):
        super().__init__(message)
        self.message = message
        self.code = code


@dataclass
class Context:
    """Everything the guards need, gathered by the caller."""
    state: str
    role: str
    actor: str
    now: datetime
    policy: dict                      # the tenant's board-approved thresholds
    doc_types: set[str]               # document types already on the case
    response_due_ts: datetime | None
    # "" when no examination has been started, else its status. See BR-414.
    accountability_status: str = ""
    # What the actor's role may do, read from the tenant's role rows by the caller.
    permissions: frozenset = frozenset()
    # The most recent still-open proposal for this action, if any.
    pending_actor: str | None = None
    pending_role: str | None = None
    # When the case entered its current state - used for the lateness check.
    state_since: datetime | None = None


def _aware(ts: datetime | None) -> datetime | None:
    """Postgres can hand back naive datetimes depending on the column; compare safely."""
    if ts is None:
        return None
    return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)


def check(action: str, ctx: Context, *, reason: str = "") -> Transition:
    """Raise TransitionRefused unless every guard passes. Returns the transition."""
    tr = BY_ACTION.get(action)
    if tr is None:
        raise TransitionRefused("Unknown action: " + action, "unknown_action")
    if ctx.state != tr.src:
        raise TransitionRefused(
            "'" + tr.label + "' applies to a case in '" + tr.src +
            "', but this case is in '" + ctx.state + "'.", "wrong_state")
    if case_action(tr.action) not in ctx.permissions:
        raise TransitionRefused(
            "Your role may not " + tr.label.lower() + ".", "role_not_permitted")
    if tr.requires_reason and not reason.strip():
        raise TransitionRefused(
            "A written reason is required for '" + tr.label + "'.", "reason_required")
    if tr.requires_document and tr.requires_document not in ctx.doc_types:
        raise TransitionRefused(
            "A '" + tr.requires_document + "' document must be attached before "
            "'" + tr.label + "'. A finding of fraud following a hearing has to be a "
            "speaking order (SBI v. Rajesh Agarwal).", "document_required")
    if tr.requires_window_elapsed:
        due = _aware(ctx.response_due_ts)
        if due is None:
            raise TransitionRefused(
                "No response window is recorded on this case.", "no_window")
        if ctx.now < due:
            raise TransitionRefused(
                "The borrower's response window is open until " +
                due.strftime("%d %b %Y %H:%M") + " and may not be cut short.",
                "window_open")
    if tr.requires_accountability and ctx.accountability_status != "concluded":
        raise TransitionRefused(
            "The staff accountability examination for this case has "
            + ("not been started." if not ctx.accountability_status
               else "not been concluded.")
            + " A fraud may be reported before accountability is settled, but it may not "
              "be closed with the question unanswered.",
            "accountability_pending")
    if tr.requires_second_approval:
        if ctx.pending_actor is None:
            raise TransitionRefused(
                "'" + tr.label + "' needs a second approver. Your proposal has been "
                "recorded; a different authorised user must confirm it.",
                "approval_pending")
        if ctx.pending_actor == ctx.actor:
            raise TransitionRefused(
                "You proposed this action, so you may not also approve it.",
                "self_approval")
        if "case.approve_transition" not in ctx.permissions:
            raise TransitionRefused(
                "Approving '" + tr.label + "' needs the approve permission, which your "
                "role has not been given.", "not_an_approver")
    return tr


def is_late(tr: Transition, ctx: Context) -> tuple[bool, int]:
    """Was this done outside the tenant's own window? Returns (late, days_allowed)."""
    if not tr.late_after_days:
        return False, 0
    allowed = int(ctx.policy.get(tr.late_after_days, 0) or 0)
    since = _aware(ctx.state_since)
    if not allowed or since is None:
        return False, allowed
    return ctx.now > since + timedelta(days=allowed), allowed


def clocks_for(tr: Transition, ctx: Context) -> dict[str, datetime]:
    """The deadlines this transition starts, in the tenant's own policy terms."""
    out: dict[str, datetime] = {}
    for key in tr.starts_clocks:
        days = int(ctx.policy.get(key, 0) or 0)
        if not days:
            continue
        if key == "natural_justice_days":
            out["response_due_ts"] = ctx.now + timedelta(days=days)
        elif key == "fmr_filing_days":
            out["fmr_due_ts"] = ctx.now + timedelta(days=days)
        elif key == "str_filing_days":
            out["str_due_ts"] = ctx.now + timedelta(days=days)
        elif key == "staff_accountability_days":
            out["accountability_due_ts"] = ctx.now + timedelta(days=days)
    return out
