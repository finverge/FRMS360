"""What the platform notifies about, and how loudly.

Every kind is declared here with its severity and whether a user may mute it. That last
column is the one worth arguing about: most notifications are a courtesy and people should
be able to turn them off, but a handful exist because a regulator expects the bank to have
acted, and an individual's preference is not a defence for having missed them.

Muting a breached natural-justice window is not a preference. It is a compliance failure
with a checkbox in front of it.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Kind:
    key: str
    label: str
    severity: str          # info | warn | urgent
    mutable: bool
    #: Written as a sentence an operator can act on, not as a status code.
    template: str


KINDS: dict[str, Kind] = {k.key: k for k in (
    Kind("case_assigned", "Case assigned to you", "info", True,
         "Case {case_id} ({severity}) has been assigned to you."),
    Kind("approval_pending", "Second approver needed", "urgent", False,
         "{proposer} has proposed declaring fraud on case {case_id}. It needs a "
         "different authorised approver before it takes effect."),
    Kind("nj_window_closing", "Response window closing", "warn", False,
         "The borrower's response window on case {case_id} closes in {days} day(s). "
         "A decision cannot be taken before it does."),
    Kind("nj_window_breached", "Response window elapsed", "urgent", False,
         "The response window on case {case_id} closed {days} day(s) ago and the case "
         "has not moved on."),
    Kind("fmr_due", "FMR filing due", "warn", False,
         "The Fraud Monitoring Return for case {case_id} is due in {days} day(s)."),
    Kind("fmr_overdue", "FMR filing overdue", "urgent", False,
         "The Fraud Monitoring Return for case {case_id} is {days} day(s) overdue."),
    Kind("str_due", "STR filing due", "warn", False,
         "The STR for case {case_id} is due in {days} day(s)."),
    Kind("str_overdue", "STR filing overdue", "urgent", False,
         "The STR for case {case_id} is {days} day(s) overdue."),
    Kind("critical_alert", "Critical alert awaiting triage", "urgent", True,
         "{count} critical alert(s) have been waiting for triage for more than "
         "{hours} hour(s)."),
    Kind("case_stalled", "Case has not moved", "warn", True,
         "Case {case_id} has been in '{state}' for {days} day(s) with no action."),
    Kind("board_pack_issued", "Board / ACB pack issued", "info", False,
         "The {period} fraud pack has been issued to the committee by {issued_by}. "
         "It is a frozen record: {cases} declared fraud(s), {overdue} return(s) "
         "overdue."),
    Kind("ingestion_stalled", "No transactions received", "urgent", True,
         "No transactions have been received for {hours} hour(s). Detection cannot "
         "see what it is not sent."),
)}

#: Kinds a person cannot switch off. Each is a regulatory clock or a dual-control step -
#: things the bank is accountable for whether or not an individual wanted the reminder.
UNMUTABLE = frozenset(k.key for k in KINDS.values() if not k.mutable)

_ORDER = {"info": 0, "warn": 1, "urgent": 2}


def at_least(severity: str, minimum: str) -> bool:
    return _ORDER.get(severity, 0) >= _ORDER.get(minimum, 0)


def render(kind_key: str, context: dict) -> tuple[str, str, str]:
    """(subject, body, severity) for a notification of this kind."""
    kind = KINDS.get(kind_key)
    if kind is None:
        return (kind_key, str(context), "info")
    try:
        body = kind.template.format(**context)
    except KeyError as exc:
        # A template referencing something the caller did not supply is a bug, but it
        # must not silence a compliance notification. Degrade to the label.
        body = f"{kind.label} (missing detail: {exc})"
    return kind.label, body, kind.severity


def is_muted(kind_key: str, muted: dict | list | None) -> bool:
    if kind_key in UNMUTABLE:
        return False
    if not muted:
        return False
    keys = muted if isinstance(muted, (list, tuple, set)) else muted.keys()
    return kind_key in set(keys)
