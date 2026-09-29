"""What the board is told, and why each part of it is there.

The Master Directions put fraud governance on the board and its Audit Committee: they are
to be told what happened, whether the institution reported it on time, whether its own
staff were examined, and what is still unresolved. This module is that agenda, written
down once.

**Every figure is a metric-registry name, never SQL.** That is deliberate and it is the
whole reason the pack can be trusted: the board's "total fraud value" is literally the
same registry entry the supervisor's dashboard reads. A pack that recomputed its own
numbers would eventually disagree with the screens the executives had already seen, and
the reconciliation would happen in the meeting.

**A section may be empty, but it is never omitted.** "No frauds above the board-reporting
floor this quarter" is a governance statement. Dropping the heading because the number was
zero would make an absence of reporting indistinguishable from an absence of frauds.
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class Section:
    key: str
    title: str
    #: Why the board is being shown this. Rendered into the pack, because a governance
    #: document that does not say why a figure is present teaches nobody.
    why: str
    metrics: tuple[str, ...] = ()
    #: (metric, dimension) pairs rendered as a breakdown table.
    breakdowns: tuple[tuple[str, str], ...] = ()


SECTIONS: tuple[Section, ...] = (
    Section(
        "position", "Fraud position for the period",
        "The headline the board is accountable for: what was lost, what was recovered, "
        "and what the institution is still exposed to.",
        metrics=("fraud_value_total", "fraud_case_count", "recovered_value",
                 "net_fraud_value", "prevented_value"),
    ),
    Section(
        "composition", "Where the frauds came from",
        "Concentration is the actionable part. A quarter's losses sitting in one product "
        "or one region is a control question, not a fraud question.",
        breakdowns=(("fraud_value_total", "fmr_category"),
                    ("fraud_value_total", "rail"),
                    ("fraud_value_total", "region"),
                    ("fraud_value_total", "product")),
    ),
    Section(
        "reporting", "Regulatory reporting compliance",
        "Whether the institution met its own filing windows. Late or unfiled returns are "
        "a supervisory finding in their own right, independent of the underlying fraud.",
        metrics=("fmr_due_count", "fmr_filed_count", "fmr_overdue_count",
                 "str_due_count", "str_filed_count", "str_overdue_count"),
    ),
    Section(
        "natural_justice", "Red-flagged accounts and due process",
        "RBI requires a hearing before an account is classified as fraud "
        "(SBI v. Rajesh Agarwal). A breach here is a legal exposure, not an admin lapse.",
        metrics=("rfa_count", "rfa_awaiting_show_cause", "nj_open_count",
                 "nj_breach_count", "fraud_without_reasoned_order"),
    ),
    Section(
        "materiality", "Cases above the board-reporting threshold",
        "Individual cases the board's own approved policy says it must see by name, "
        "rather than inside an aggregate.",
        metrics=("above_board_threshold_count", "above_lea_threshold_count"),
    ),
    Section(
        "accountability", "Staff accountability examinations",
        "Whether the institution examined its own people. Reported separately from the "
        "return, because the Directions require it to happen without delaying reporting.",
    ),
    Section(
        "operations", "Case handling and backlog",
        "Whether cases are actually being worked. A stable fraud figure with a rising "
        "backlog is a deteriorating position that the headline number hides.",
        metrics=("open_case_count", "stalled_case_count", "oldest_open_case_days",
                 "sla_breach_count", "untouched_alert_count"),
    ),
    Section(
        "detection", "Early-warning coverage",
        "What the detection estate actually did. Indicators configured but never firing "
        "are EWS coverage that exists on paper only - the thing an inspection looks for.",
        metrics=("alert_count", "true_positive_count", "false_positive_count",
                 "precision", "active_rule_count", "critical_alert_count"),
    ),
)

BY_KEY = {s.key: s for s in SECTIONS}

#: Cadences a pack can be produced on, in months. The tenant's own
#: ``board_review_frequency`` selects one; RBI expects at least quarterly review.
CADENCE_MONTHS = {"monthly": 1, "quarterly": 3, "half_yearly": 6, "annual": 12}
