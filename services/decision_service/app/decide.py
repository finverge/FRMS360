"""Turning an evaluation into an action, and recording what happened.

The action is derived from the policy, never from the rules: the same match produces a
challenge on one rail and a decline on another, because what a channel can carry is the
bank's business. Keeping that here rather than in the catalogue is what lets one rule
definition serve both lanes.
"""
from __future__ import annotations

import uuid

from cp_common import scoring
from dataclasses import dataclass

from .evaluate import Evaluation
from .policy import FAIL_CLOSED, MODE_INLINE, RailPolicy

SEVERITY_ORDER = {"low": 0, "medium": 1, "high": 2, "critical": 3}


@dataclass
class Decision:
    action: str
    outcome: str
    enforced: bool
    matched: list
    skipped: list
    took_ms: float
    lookup_ms: float
    evaluate_ms: float
    budget_ms: int
    policy_version: str
    reference: str
    #: What the lane *would* have done had it been enforcing. In shadow mode this
    #: differs from ``action`` — and that difference is the entire measurement.
    would_be: str = "allow"
    #: The alert-level score and severity, computed exactly as the near-real-time lane
    #: computes them. Carried on the record so a decline can be explained in the same
    #: terms as an alert, months later.
    score: float = 0.0
    severity: str = "low"


def severity_of(matched: list, frm_policy: dict | None = None) -> tuple[float, str]:
    """Score and band the matched rules, using the shared definition.

    This used to read a ``severity`` field off each matched rule. The catalogue carries no
    such field, so every match banded as unstated, the decline floor was never met, and
    the lane could not decline anything however dangerous the payment. Severity is an
    alert-level property derived from a score over rule families — one velocity hit is not
    critical; velocity with layering and structuring is.
    """
    score = scoring.score_of(m.get("family", "") for m in matched)
    return score, scoring.severity_for(score, frm_policy)


def decide(*, ev: Evaluation, rp: RailPolicy, policy_version: str,
           total_ms: float, frm_policy: dict | None = None) -> Decision:
    """Resolve an evaluation into an action under one rail's policy."""
    ref = uuid.uuid4().hex[:16]
    score, severity = severity_of(ev.matched, frm_policy)

    def _mk(action: str, outcome: str) -> Decision:
        # Shadow mode always tells the caller to allow. The computed action is carried
        # separately so the bank can compare what would have happened against what did.
        enforced = not rp.shadow and rp.mode == MODE_INLINE
        return Decision(
            action=action if enforced else "allow",
            would_be=action,
            outcome=outcome,
            enforced=enforced,
            matched=ev.matched, skipped=ev.skipped,
            score=score, severity=severity,
            took_ms=total_ms, lookup_ms=ev.lookup_ms, evaluate_ms=ev.evaluate_ms,
            budget_ms=rp.budget_ms, policy_version=policy_version, reference=ref)

    # The check could not complete. This is the branch the fail policy exists for, and
    # it is recorded distinctly from a clean pass — "allowed because nothing matched"
    # and "allowed because we ran out of time" must never look the same in the log.
    if not ev.store_available:
        return _mk("decline" if rp.fail == FAIL_CLOSED else "allow", "store_unavailable")
    if ev.budget_exceeded:
        return _mk("decline" if rp.fail == FAIL_CLOSED else "allow", "budget_exceeded")

    # Nothing was evaluated. An empty catalogue is a configuration failure, and it is the
    # most dangerous one this lane has: with no rules, every payment matches nothing, and
    # "allow / clean" is indistinguishable from a payment that passed every check. The
    # rail's own fail policy decides what happens, exactly as it does when the counter
    # store is down - because this is the same kind of event.
    #
    # ``not ev.matched`` guards it: if something matched then rules plainly ran, whatever
    # the counter says. Trusting the counter alone would risk declining a genuine payment
    # on a fail-closed rail because of a bookkeeping slip in this service.
    if ev.considered == 0 and not ev.matched:
        return _mk("decline" if rp.fail == FAIL_CLOSED else "allow", "not_screened")

    if not ev.matched:
        return _mk("allow", "clean")

    # Something matched. Escalate to the strongest action the rail permits, capped by
    # the configured severity floor for declining.
    if "decline" in rp.actions and scoring.at_least(severity, rp.decline_from_severity):
        return _mk("decline", "matched")
    for candidate in ("challenge", "hold"):
        if candidate in rp.actions:
            return _mk(candidate, "matched")
    # The rail permits only decline, and the severity floor was not met.
    return _mk("allow", "matched")
