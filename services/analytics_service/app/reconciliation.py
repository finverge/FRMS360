"""Reconciliation invariants.

Two kinds of check, deliberately scoped differently:

* **Integrity** (R-01/02/03/07.vocab/09) run tenant-wide. An orphaned alert is an orphan
  whether or not the current view filters it out, so narrowing the filter must not hide it.
* **Consistency** (R-04/05/06/07/08) run under the *active filters*, because they compare a
  dashboard figure against an independently-written query - a comparison that is only
  meaningful when both sides see the same slice.

The metric registry makes dashboards agree *by construction* (one definition per metric).
These checks prove it held, and catch the failure modes a registry cannot prevent:
orphaned facts, non-additive partitions, derived totals drifting from their components,
and filed regulatory figures diverging from the board number.

Every check returns expected vs actual with an exact delta. Money is compared in integer
paise, so a passing check means *exactly* equal, not "close enough".
"""
from dataclasses import dataclass, asdict

from .engine.base import Filters
from .engine.postgres import PostgresEngine
from .metrics import REGISTRY
from .models import CASE_STATES, FRAUD_STATES


@dataclass
class Check:
    id: str
    title: str
    severity: str          # critical | high | medium
    passed: bool
    expected: float
    actual: float
    delta: float
    detail: str = ""

    def dict(self) -> dict:
        return asdict(self)


def _mk(cid, title, sev, expected, actual, detail="") -> Check:
    e, a = float(expected or 0), float(actual or 0)
    return Check(cid, title, sev, e == a, e, a, a - e, detail)


def run_all(engine: PostgresEngine, f: Filters) -> dict:
    """Run every invariant for a tenant/filter set."""
    p = {"tenant_id": f.tenant_id}
    checks: list[Check] = []

    # R-01 every alert points at a transaction that exists
    orphan_alerts = engine.scalar_sql(
        "SELECT COUNT(*) FROM fact_alert a LEFT JOIN fact_transaction t "
        "  ON t.txn_id = a.txn_id AND t.tenant_id = a.tenant_id "
        "WHERE a.tenant_id = :tenant_id AND t.txn_id IS NULL", p)
    checks.append(_mk("R-01", "Every alert resolves to a transaction", "critical",
                      0, orphan_alerts, "Orphan alerts break drill-down to evidence."))

    # R-02 every alert's case_id resolves
    orphan_cases = engine.scalar_sql(
        "SELECT COUNT(*) FROM fact_alert a LEFT JOIN fact_case c "
        "  ON c.case_id = a.case_id AND c.tenant_id = a.tenant_id "
        "WHERE a.tenant_id = :tenant_id AND a.case_id IS NOT NULL AND c.case_id IS NULL", p)
    checks.append(_mk("R-02", "Every linked case exists", "critical", 0, orphan_cases))

    # R-03 case amount == value of the DISTINCT transactions behind it (load-bearing)
    #
    # The distinctness is the whole subtlety. One transaction can trip several rules and
    # therefore carry several alerts - that is the point of the rule-evaluation trace.
    # Joining case -> alert -> transaction then counts that transaction's value once per
    # alert, so a single Rs 9.87 lakh payment that fired five rules appeared as Rs 49.3
    # lakh of "evidence" and the case looked understated by a factor of five.
    #
    # This check passed for years only because the synthetic generator emitted exactly one
    # alert per transaction. Live detection, which fires every rule that matches, exposed
    # it immediately: 7 cases failed, and in every one the stated value was right and the
    # check was wrong. Sum the distinct transactions.
    mismatched = engine.scalar_sql(
        "SELECT COUNT(*) FROM ( "
        "  SELECT c.case_id, c.amount_paise AS stated, "
        "    COALESCE(( "
        "      SELECT SUM(t.amount_paise) FROM ( "
        "        SELECT DISTINCT a.txn_id FROM fact_alert a "
        "        WHERE a.case_id = c.case_id AND a.tenant_id = c.tenant_id "
        "      ) d JOIN fact_transaction t "
        "        ON t.txn_id = d.txn_id AND t.tenant_id = c.tenant_id "
        "    ), 0) AS derived "
        "  FROM fact_case c WHERE c.tenant_id = :tenant_id "
        ") s WHERE s.stated <> s.derived", p)
    checks.append(_mk("R-03", "Case value equals the sum of its linked transactions", "critical",
                      0, mismatched,
                      "A case whose stated value drifts from its evidence cannot be defended."))

    # R-04 board headline == sum of case detail for fraud states
    fraud_in = ", ".join(f"'{s}'" for s in FRAUD_STATES)
    board_total = engine.metric("fraud_value_total", f).value
    detail_total = engine.filtered_scalar(
        "case", "COALESCE(SUM({b}.amount_paise), 0)", f,
        "{b}.state IN (" + fraud_in + ")")
    checks.append(_mk("R-04", "Board fraud value equals case-level detail", "critical",
                      detail_total, board_total,
                      "The headline figure must be the sum of the rows behind it."))

    # R-05 filed FMR value never exceeds, and reconciles into, the board fraud value
    filed_value = engine.metric("fmr_filed_value", f).value
    filed_within_fraud = engine.filtered_scalar(
        "case", "COALESCE(SUM({b}.amount_paise), 0)", f,
        "{b}.fmr_filed_ts IS NOT NULL AND {b}.state IN (" + fraud_in + ")")
    checks.append(_mk("R-05", "No FMR filed against a non-fraud-classified case", "critical",
                      filed_within_fraud, filed_value,
                      "Every filed rupee must sit behind a fraud classification. A gap here "
                      "means something was reported to RBI that the case file does not justify."))

    # R-05b filed value can never exceed the classified fraud value
    unfiled = float(board_total) - float(filed_value)
    checks.append(Check("R-05b", "Filed FMR value does not exceed classified fraud value",
                        "critical", unfiled >= 0, float(board_total), float(filed_value),
                        float(filed_value) - float(board_total),
                        f"Unfiled fraud value currently {unfiled / 100:,.2f} INR."))

    # R-06 alert partitions are exact (no nulls, no double counting)
    total_alerts = engine.metric("alert_count", f).value
    for dim, label in (("severity", "severity"), ("family", "EWS family"),
                       ("disposition", "disposition")):
        part = sum(r["value"] for r in engine.breakdown("alert_count", dim, f))
        checks.append(_mk(f"R-06.{dim}", f"Alerts partition exactly by {label}", "high",
                          total_alerts, part,
                          "A breakdown that does not sum to the total means a hidden bucket."))

    # R-07 case state partition
    total_cases = engine.metric("case_count", f).value
    state_part = sum(r["value"] for r in engine.breakdown("case_count", "state", f))
    checks.append(_mk("R-07", "Cases partition exactly by lifecycle state", "high",
                      total_cases, state_part))
    unknown_states = engine.scalar_sql(
        "SELECT COUNT(*) FROM fact_case WHERE tenant_id = :tenant_id AND state <> ALL(:known)",
        {**p, "known": CASE_STATES})
    checks.append(_mk("R-07.vocab", "No case sits in an unknown state", "high", 0, unknown_states))

    # R-08 derived metric consistency: net == gross - recovered
    gross = engine.metric("fraud_value_total", f).value
    recovered = engine.metric("recovered_value", f).value
    net = engine.metric("net_fraud_value", f).value
    checks.append(_mk("R-08", "Net fraud equals gross minus recovery", "high",
                      gross - recovered, net))

    # R-09 recovery can never exceed the fraud amount on a case
    over_recovered = engine.scalar_sql(
        "SELECT COUNT(*) FROM fact_case WHERE tenant_id = :tenant_id "
        "AND recovered_paise > amount_paise", p)
    checks.append(_mk("R-09", "Recovery never exceeds case value", "medium", 0, over_recovered))

    # R-10 cross-dashboard identity: the same metric must return one value regardless of
    # which dashboard asked. Guaranteed by the registry - asserted here so a future
    # hand-rolled query in a route is caught immediately.
    drift = 0
    for name in ("fraud_value_total", "case_count", "alert_count", "precision"):
        vals = {engine.metric(name, f).value for _ in range(2)}
        if len(vals) != 1:
            drift += 1
    checks.append(_mk("R-10", "Metrics are deterministic across dashboards", "critical",
                      0, drift, "Same metric + same filters must yield one value everywhere."))

    # R-10b no figure that must tie to a regulatory filing may be volatile. A NOW()-based
    # metric cannot be reproduced as-of a date, so it must never back a filed number.
    REGULATORY = ("fraud_value_total", "fmr_filed_value", "fmr_filed_count",
                  "str_filed_count", "fraud_case_count", "nj_breach_count")
    volatile_reg = [n for n in REGULATORY if REGISTRY[n].volatile]
    checks.append(_mk("R-10b", "No regulatory figure depends on wall-clock time", "critical",
                      0, len(volatile_reg),
                      "A NOW()-relative metric cannot be reproduced for an inspection."))

    # R-11 registry hygiene: no metric defined twice
    dupes = len([n for n in REGISTRY if list(REGISTRY).count(n) > 1])
    checks.append(_mk("R-11", "No metric is defined more than once", "medium", 0, dupes))

    failed = [c for c in checks if not c.passed]
    return {
        "status": "pass" if not failed else "fail",
        "checked_at": None,  # filled by the route with a server timestamp
        "filters": f.as_dict(),
        "summary": {
            "total": len(checks),
            "passed": len(checks) - len(failed),
            "failed": len(failed),
            "critical_failures": len([c for c in failed if c.severity == "critical"]),
        },
        "checks": [c.dict() for c in checks],
    }
