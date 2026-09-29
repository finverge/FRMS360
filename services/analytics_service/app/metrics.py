"""THE metric registry.

Every dashboard figure — analyst, board or supervisor — is looked up by name from this
one registry. There is exactly one SQL definition per metric, so the same metric under
the same filters is literally the same query on every screen. Dashboards therefore
reconcile *by construction*; the checks in ``reconciliation.py`` verify it rather than
create it.

Adding a metric in a route handler instead of here is the one thing that breaks the
guarantee, so routes have no SQL of their own.
"""
from dataclasses import dataclass

from .models import FRAUD_STATES


@dataclass(frozen=True)
class Metric:
    name: str
    label: str
    unit: str            # inr_paise | count | ratio (0..1, shown as %) | number | seconds
    base: str            # case | alert | transaction  (the fact table)
    expr: str            # SQL aggregate over that fact
    where: str = ""      # metric-intrinsic predicate, ANDed with user filters
    description: str = ""
    # True when the value depends on wall-clock NOW() and therefore changes between
    # calls. Such a metric is a point-in-time reading: it cannot be reconciled across
    # dashboards, and it cannot be reproduced "as of" a past date. Keep these OUT of
    # anything that has to tie to a filed regulatory figure.
    volatile: bool = False


_FRAUD_IN = ", ".join(f"'{s}'" for s in FRAUD_STATES)
# NB: '{b}' is a placeholder the engine substitutes with its own table alias. These must
# stay plain strings (never f-strings), or the brace would be evaluated at import time.
_FRAUD_WHERE = "{b}.state IN (" + _FRAUD_IN + ")"

REGISTRY: dict[str, Metric] = {}


def _add(m: Metric) -> None:
    REGISTRY[m.name] = m


# ---------------- Loss, recovery, exposure (board) ----------------
_add(Metric("fraud_value_total", "Total fraud value", "inr_paise", "case",
            "COALESCE(SUM({b}.amount_paise), 0)", _FRAUD_WHERE,
            "Value of cases classified as fraud. Ties to FMR filings."))
_add(Metric("fraud_case_count", "Fraud cases", "count", "case",
            "COUNT(*)", _FRAUD_WHERE))
_add(Metric("recovered_value", "Recovered value", "inr_paise", "case",
            "COALESCE(SUM({b}.recovered_paise), 0)", _FRAUD_WHERE))
_add(Metric("net_fraud_value", "Net fraud value", "inr_paise", "case",
            "COALESCE(SUM({b}.amount_paise - {b}.recovered_paise), 0)", _FRAUD_WHERE,
            "Gross fraud less recovery."))
_add(Metric("prevented_value", "Prevented value", "inr_paise", "transaction",
            "COALESCE(SUM({b}.amount_paise), 0)", "{b}.status = 'interdicted'",
            "Value stopped before settlement - the platform's ROI figure."))
_add(Metric("throughput_value", "Throughput value", "inr_paise", "transaction",
            "COALESCE(SUM({b}.amount_paise), 0)"))
_add(Metric("transaction_count", "Transactions", "count", "transaction", "COUNT(*)"))

# ---------------- Case population (board + supervisor) ----------------
_add(Metric("case_count", "Cases", "count", "case", "COUNT(*)"))
_add(Metric("rfa_count", "Red-flagged accounts", "count", "case",
            "COUNT(*)", "{b}.rfa_flag = true"))
_add(Metric("open_case_count", "Open cases", "count", "case",
            "COUNT(*)", "{b}.state NOT IN ('closed_fraud', 'exonerated')"))

# ---------------- Detection effectiveness (analyst + supervisor) ----------------
_add(Metric("alert_count", "Alerts", "count", "alert", "COUNT(*)"))
_add(Metric("pending_alert_count", "Pending alerts", "count", "alert",
            "COUNT(*)", "{b}.disposition = 'pending'"))
_add(Metric("true_positive_count", "True positives", "count", "alert",
            "COUNT(*)", "{b}.disposition = 'true_positive'"))
_add(Metric("false_positive_count", "False positives", "count", "alert",
            "COUNT(*)", "{b}.disposition = 'false_positive'"))
_add(Metric("precision", "Precision", "ratio", "alert",
            "CASE WHEN COUNT(*) FILTER (WHERE {b}.disposition IN ('true_positive','false_positive')) = 0 "
            "THEN 0 ELSE COUNT(*) FILTER (WHERE {b}.disposition = 'true_positive')::float "
            "/ COUNT(*) FILTER (WHERE {b}.disposition IN ('true_positive','false_positive')) END",
            "", "TP / (TP + FP) over dispositioned alerts only."))

# ---------------- Speed (analyst + supervisor) ----------------
_add(Metric("mtta_seconds", "Mean time to acknowledge", "seconds", "alert",
            "COALESCE(AVG(EXTRACT(EPOCH FROM ({b}.first_touch_ts - {b}.ts))), 0)",
            "{b}.first_touch_ts IS NOT NULL"))
_add(Metric("mtti_seconds", "Mean time to investigate", "seconds", "alert",
            "COALESCE(AVG(EXTRACT(EPOCH FROM ({b}.disposition_ts - {b}.ts))), 0)",
            "{b}.disposition_ts IS NOT NULL"))

# ---------------- Compliance clocks (supervisor + board) ----------------
_add(Metric("fmr_due_count", "FMR due", "count", "case",
            "COUNT(*)", "{b}.fmr_due_ts IS NOT NULL"))
_add(Metric("fmr_filed_count", "FMR filed", "count", "case",
            "COUNT(*)", "{b}.fmr_filed_ts IS NOT NULL"))
_add(Metric("fmr_filed_value", "FMR filed value", "inr_paise", "case",
            "COALESCE(SUM({b}.amount_paise), 0)", "{b}.fmr_filed_ts IS NOT NULL",
            "Must tie to the board's fraud value for filed cases."))
_add(Metric("fmr_overdue_count", "FMR overdue", "count", "case",
            "COUNT(*)", "{b}.fmr_due_ts IS NOT NULL AND {b}.fmr_filed_ts IS NULL AND {b}.fmr_due_ts < NOW()", volatile=True))
_add(Metric("str_due_count", "STR due", "count", "case", "COUNT(*)", "{b}.str_due_ts IS NOT NULL"))
_add(Metric("str_filed_count", "STR filed", "count", "case", "COUNT(*)", "{b}.str_filed_ts IS NOT NULL"))
_add(Metric("str_overdue_count", "STR overdue", "count", "case",
            "COUNT(*)", "{b}.str_due_ts IS NOT NULL AND {b}.str_filed_ts IS NULL AND {b}.str_due_ts < NOW()", volatile=True))
_add(Metric("nj_breach_count", "Natural-justice window breached", "count", "case",
            "COUNT(*)",
            "{b}.response_due_ts IS NOT NULL AND {b}.decision_ts IS NOT NULL AND {b}.decision_ts > {b}.response_due_ts",
            "Cases decided after the borrower response window closed - direct exposure."))
_add(Metric("nj_open_count", "Awaiting borrower response", "count", "case",
            "COUNT(*)", "{b}.state = 'natural_justice'"))
# A show-cause timestamp is not evidence that a reasoned order exists. This counts cases
# classified as fraud with no such document on file - the gap an inspection looks for.
_add(Metric("fraud_without_reasoned_order", "Fraud without a reasoned order", "count", "case",
            "COUNT(*)",
            "{b}.state IN (" + _FRAUD_IN + ") AND NOT EXISTS ("
            "SELECT 1 FROM case_documents d WHERE d.case_id = {b}.case_id "
            "AND d.tenant_id = {b}.tenant_id AND d.doc_type = 'reasoned_order')",
            "Fraud classification without the reasoned order that justifies it."))
_add(Metric("cases_with_documents", "Cases with evidence attached", "count", "case",
            "COUNT(*)",
            "EXISTS (SELECT 1 FROM case_documents d WHERE d.case_id = {b}.case_id "
            "AND d.tenant_id = {b}.tenant_id)"))


# ---------------- Operations / queue health (risk manager) ----------------
_add(Metric("untouched_alert_count", "Untouched alerts", "count", "alert",
            "COUNT(*)", "{b}.first_touch_ts IS NULL AND {b}.disposition = 'pending'",
            "Raised but never opened by anyone - the true queue-health signal."))
_add(Metric("dispositioned_count", "Dispositioned alerts", "count", "alert",
            "COUNT(*)", "{b}.disposition <> 'pending'"))
_add(Metric("backlog_age_p95_hours", "Backlog age (P95)", "seconds", "alert",
            "COALESCE(PERCENTILE_CONT(0.95) WITHIN GROUP "
            "(ORDER BY EXTRACT(EPOCH FROM (NOW() - {b}.ts))), 0)",
            "{b}.disposition = 'pending'",
            "Averages hide the tail; the tail is the risk.", volatile=True))
_add(Metric("sla_breach_count", "SLA breaches", "count", "alert",
            "COUNT(*)",
            "{b}.disposition = 'pending' "
            "AND {b}.ts < NOW() - make_interval(hours => {sla_breach_hours})",
            "Pending beyond this tenant's board-approved SLA window.", volatile=True))
# The regulatory ceiling on examining an EWS alert, as distinct from the bank's own
# operational SLA above. An alert past this window is a compliance exposure, not a
# service-level miss, which is why it is counted separately rather than folded in.
_add(Metric("ews_examination_overdue", "EWS alerts past the 30-day examination window",
            "count", "alert", "COUNT(*)",
            "{b}.first_touch_ts IS NULL "
            "AND {b}.ts < NOW() - make_interval(days => {ews_examination_days})",
            "Raised but never examined, beyond the window the Directions prescribe.",
            volatile=True))
_add(Metric("ews_examined_late_count", "EWS alerts examined late", "count", "alert",
            "COUNT(*)",
            "{b}.first_touch_ts IS NOT NULL "
            "AND {b}.first_touch_ts > {b}.ts + make_interval(days => {ews_examination_days})",
            "Examined, but after the window closed. Recorded rather than forgiven - a "
            "breach that has since been cleared is still a breach an inspection counts."))
_add(Metric("ews_examination_p95_days", "EWS examination time (P95)", "seconds", "alert",
            "COALESCE(PERCENTILE_CONT(0.95) WITHIN GROUP "
            "(ORDER BY EXTRACT(EPOCH FROM ({b}.first_touch_ts - {b}.ts))), 0)",
            "{b}.first_touch_ts IS NOT NULL",
            "How long examination actually takes. The mean would hide the tail, and the "
            "tail is what breaches."))

# ---------------- Investigation / network (senior investigator) ----------------
_add(Metric("layering_alert_count", "Layering / mule alerts", "count", "alert",
            "COUNT(*)", "{b}.rule_family = 'LAY'",
            "LAY family - the cross-transaction ring signals."))
_add(Metric("critical_alert_count", "Critical alerts", "count", "alert",
            "COUNT(*)", "{b}.severity = 'critical'"))
_add(Metric("above_lea_threshold_count", "Above LEA referral floor", "count", "case",
            "COUNT(*)", "{b}.amount_paise >= {lea_referral_paise}",
            "Cases at or above this tenant's law-enforcement referral floor."))
_add(Metric("above_board_threshold_count", "Above board-reporting floor", "count", "case",
            "COUNT(*)", "{b}.amount_paise >= {board_reporting_paise}"))
_add(Metric("alerts_per_case", "Alerts per case", "number", "alert",
            "CASE WHEN COUNT(DISTINCT {b}.case_id) = 0 THEN 0 "
            "ELSE COUNT(*)::float / COUNT(DISTINCT {b}.case_id) END",
            "{b}.case_id IS NOT NULL",
            "A proxy for ring size: many alerts on one case suggests a network."))
_add(Metric("linked_account_count", "Accounts involved", "count", "transaction",
            "COUNT(DISTINCT {b}.debtor_account)"))
_add(Metric("counterparty_count", "Distinct counterparties", "count", "transaction",
            "COUNT(DISTINCT {b}.creditor_account)"))

# ---------------- AML / PMLA (principal officer) ----------------
_add(Metric("str_pending_count", "STR pending", "count", "case",
            "COUNT(*)", "{b}.str_due_ts IS NOT NULL AND {b}.str_filed_ts IS NULL"))
_add(Metric("str_filed_value", "STR filed value", "inr_paise", "case",
            "COALESCE(SUM({b}.amount_paise), 0)", "{b}.str_filed_ts IS NOT NULL"))

# ---------------- Model risk / FREE-AI (data science) ----------------
_add(Metric("avg_score", "Mean score", "number", "alert",
            "COALESCE(AVG({b}.score), 0)"))
_add(Metric("score_p95", "Score P95", "number", "alert",
            "COALESCE(PERCENTILE_CONT(0.95) WITHIN GROUP (ORDER BY {b}.score), 0)"))
_add(Metric("config_version_count", "Config versions in use", "count", "alert",
            "COUNT(DISTINCT {b}.config_version)",
            "", "More than one live version means scores are not comparable across the set."))


# ---------------- EWS signal health (D-02) ----------------
_add(Metric("active_rule_count", "Rules firing", "count", "alert",
            "COUNT(DISTINCT {b}.rule_id)", "",
            "Distinct rules that produced at least one alert in the period. Compare with "
            "the configured rule set - a configured rule that never fires is an "
            "inspection finding."))
_add(Metric("active_family_count", "EWS families firing", "count", "alert",
            "COUNT(DISTINCT {b}.rule_family)"))
_add(Metric("typology_count", "Typologies firing", "count", "alert",
            "COUNT(DISTINCT {b}.typology)"))

# ---------------- RFA lifecycle (D-04) ----------------
_add(Metric("rfa_awaiting_show_cause", "RFA without show-cause", "count", "case",
            "COUNT(*)", "{b}.rfa_flag = true AND {b}.show_cause_ts IS NULL",
            "Red-flagged but the borrower has not been put on notice - the natural-justice "
            "clock has not even started."))
_add(Metric("stalled_case_count", "Stalled cases (>30d)", "count", "case",
            "COUNT(*)",
            "{b}.state NOT IN ('closed_fraud', 'exonerated') "
            "AND {b}.opened_ts < NOW() - make_interval(days => {stalled_case_days})",
            "Open beyond this tenant's stalled-case threshold.", volatile=True))
_add(Metric("decided_case_count", "Cases decided", "count", "case",
            "COUNT(*)", "{b}.decision_ts IS NOT NULL"))

# ---------------- Real-time transaction monitoring (D-06) ----------------
_add(Metric("interdicted_count", "Interdicted", "count", "transaction",
            "COUNT(*)", "{b}.status = 'interdicted'"))
_add(Metric("settled_count", "Settled", "count", "transaction",
            "COUNT(*)", "{b}.status = 'settled'"))
_add(Metric("interdiction_rate", "Interdiction rate", "ratio", "transaction",
            "CASE WHEN COUNT(*) = 0 THEN 0 ELSE "
            "COUNT(*) FILTER (WHERE {b}.status = 'interdicted')::float / COUNT(*) END"))
_add(Metric("distinct_device_count", "Distinct devices", "count", "transaction",
            "COUNT(DISTINCT {b}.device_id)"))

# ---------------- Platform / tenant health (D-11) ----------------
_add(Metric("ingestion_lag_seconds", "Ingestion lag", "seconds", "transaction",
            "COALESCE(EXTRACT(EPOCH FROM (NOW() - MAX({b}.ts))), 0)", "",
            "Age of the newest transaction. A growing lag means the feed is behind.", volatile=True))
_add(Metric("oldest_open_case_days", "Oldest open case", "seconds", "case",
            "COALESCE(EXTRACT(EPOCH FROM (NOW() - MIN({b}.opened_ts))), 0)",
            "{b}.state NOT IN ('closed_fraud', 'exonerated')", volatile=True))


def get_metric(name: str) -> Metric:
    try:
        return REGISTRY[name]
    except KeyError:
        raise KeyError(f"Unknown metric '{name}'. Known: {', '.join(sorted(REGISTRY))}")


def catalogue() -> list[dict]:
    return [
        {"name": m.name, "label": m.label, "unit": m.unit, "base": m.base,
         "description": m.description, "volatile": m.volatile}
        for m in sorted(REGISTRY.values(), key=lambda x: x.name)
    ]
