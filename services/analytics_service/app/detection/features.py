"""Observations, computed from the transaction stream.

A rule in the EWS catalogue is a threshold against a named observation - ``outflow_ratio``
0.90, ``distinct_counterparties`` 40, ``dormancy_days`` 180. The catalogue owns the
threshold; this owns the measurement. Keeping them apart is what lets a bank retune a band
in the console and change what fires, without anyone touching detection code.

**What is honestly computable here, and what is not.** Everything below is derived from
the payment stream itself. Several catalogue indicators cannot be: a sanctions match needs
a list, duplicate collateral needs a charge registry, trade-based typologies need invoices,
loan-misuse needs the CBS. Those are left unmeasured *on purpose* rather than approximated
- an indicator that quietly fires on a guess is worse than one that visibly never fires,
because the dormant-indicator register is exactly what an inspection reads to find EWS
coverage that exists only on paper.

Window semantics, and a trap worth naming. Observations are measured against the batch's
own high-water timestamp rather than each row's; live traffic arrives within seconds of
occurring so the two agree, and for a large historical backfill they do not.

More importantly, the batch must be able to see *itself*. Structuring is seven transfers
in an afternoon and a fan-in hub is forty payers in an hour - both usually arrive in one
file. Measuring only against history that predates the batch made every such burst
invisible: each transaction looked lonely because its accomplices had not been written
yet. So windows are computed after the batch is projected. The 30-day baseline is the
exception and deliberately excludes it, because a burst must not be allowed to dilute the
baseline it is supposed to stand out against.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta

from sqlalchemy import text
from sqlalchemy.orm import Session

# ---------------------------------------------------------------------------------
# The measurement semantics live in cp_common.observations so the near-real-time engine
# and the inline decision lane call one implementation rather than two. Re-exported here
# because every existing caller imports them from this module.
from cp_common.observations import (  # noqa: E402,F401
    CREDITOR_SIDE,
    CTR_PAISE,
    HIGH_VALUE_PAISE,
    SMALL_CREDIT_PAISE,
    UPI_PINLESS_PAISE,
    AccountContext,
    observe,
)
from cp_common.observations import _aware  # noqa: E402,F401


#: Indicators this engine can measure from payments alone.
#:
#: LAY-03 and CHN-03 were previously listed as needing external data and did not. A
#: circular flow is a property of our own payment graph, and the hour of a transaction is
#: on the transaction - the entry for CHN-03 described what the rule's *name* suggests
#: rather than what its *measurement* requires. Both are computed in graph_features.
COMPUTABLE = (
    "VEL-01", "VEL-02", "VEL-03",
    "SME-01", "SME-02",
    "BEH-01",
    "LAY-01", "LAY-02", "LAY-03", "LAY-04",
    "CPT-01",
    "CHN-01", "CHN-03",
)

#: Indicators that become measurable once the tenant loads the reference list named here.
#: Distinct from NEEDS_EXTERNAL_DATA because the gap is closable by an upload rather than
#: by a source-system integration - and until it is closed they must stay unmeasured, not
#: score zero. Reporting "no sanctions matches" to a bank that never loaded a list is the
#: most dangerous silent success available in this domain.
NEEDS_REFERENCE_DATA = {
    "CPT-02": "sanctions",
    "CPT-03": "cersai_charges",
}

#: Why the rest do not fire. Surfaced to operators so "dormant" never reads as "broken".
#: Each of these needs *records* from a source system, which is what file intake carries -
#: no list upload closes them.
NEEDS_EXTERNAL_DATA = {
    "SME-03": "requires invoice-level data to identify a split invoice",
    "CHN-02": "requires geolocation on the session, not present on the payment",
    # AI/ML roadmap Phase 1 (BRD OD-06/s19). The measurement itself is built (see
    # observe() in cp_common.observations) and fires the moment a transaction carries
    # the score - reported unmeasurable because no tenant has an integration (VideoPD,
    # Human Fraud Detection Framework) supplying it yet, the same honest gap CHN-02 is
    # in for geolocation.
    "CHN-04": "requires a device-posture risk score from an integrated "
              "device-fingerprinting provider, not present on the payment",
    "CHN-05": "requires a behavioural-biometric anomaly score from an integrated "
              "provider, not present on the payment",
    # The trade-finance and core-banking families. These were previously in neither the
    # computable set nor this register, which meant they showed up as "dormant" with no
    # explanation - indistinguishable from a rule that is configured, measurable and
    # simply never firing. That is the exact confusion the register exists to prevent,
    # and it also made the reported coverage figure meaningless.
    "TBM-01": "requires trade-finance records: the import leg of a merchanting trade is "
              "not visible on the payment",
    "TBM-02": "requires letter-of-credit data and a related-party register",
    "TBM-03": "requires the outstanding foreign bills position from trade finance",
}

#: Indicators fed by the CBS / loan-system event stream (BR-211). Measurable once the
#: tenant sends the named event kinds; until then the engine reports precisely which
#: feed is missing rather than leaving them silently dormant.
NEEDS_CBS_FEED = {
    "BEH-02": "loan_repayment events with a funding source",
    "BEH-03": "sale_proceeds events with routing",
    "CBS-01": "loan_disbursement and loan_utilisation events",
    "CBS-02": "loan_disbursement and cash_withdrawal events",
    "CBS-03": "loan_utilisation events and a connected-group register",
    "CBS-04": "cheque_return events",
    "CBS-05": "od_position events with a sanctioned limit",
    "CBS-06": "bg_lc_event events",
    "CBS-07": "facility_sanction events flagged funds_interest",
    # Same two-independent-dependencies shape as CPT-03: loan_repayment is already
    # required for BEH-02, but CBS-08 additionally needs those events to carry a
    # due_date, which most CBS extracts don't send yet.
    "CBS-08": "loan_repayment events carrying a due_date",
    # CPT-03 has two independent dependencies, unlike everything else here - it also
    # needs a CERSAI list loaded (NEEDS_REFERENCE_DATA below). Both are named so neither
    # gap reads as the whole story.
    "CPT-03": "collateral_valuation events carrying a matchable collateral_id",
}

#: Loan-origination indicators (BR-214), fed by loan_application / collateral_valuation
#: CBS events - distinct from NEEDS_CBS_FEED because these are evaluated in their own
#: pass over pending events, not folded into payment-transaction scoring. See
#: los_features.py and engine.py's evaluate_los.
NEEDS_LOS_FEED = {
    "LOS-01": "loan_application events with declared income",
    "LOS-02": "collateral_valuation events",
    "LOS-03": "loan_application events with an applicant identity",
}

#: AI/ML roadmap Phase 2/3 (BRD OD-06/s19; HLD AD-14/AD-15/s16). Scored by the tenant's
#: own active `model` config version - conditionally measurable per tenant, the same
#: shape NEEDS_REFERENCE_DATA has for a sanctions list, not "never measurable" like
#: NEEDS_EXTERNAL_DATA. The value is the model's *name* (not a generic marker) - the
#: config kind allows several differently-named `model` versions active at once, so
#: every caller (engine.py, rules.active_model) reads which one backs which rule from
#: here rather than hard-coding the name a second time. engine.py checks this against
#: whether the named model actually loaded for the batch, the same way it checks
#: screening availability for NEEDS_REFERENCE_DATA.
NEEDS_MODEL = {
    "VEL-04": "velocity-anomaly",
    # LAY-05 (Phase 3, HLD AD-15) is checked against this same set for whether the
    # named model is active at all, but is NOT scored the way VEL-04 is - see
    # engine.py: LAY-05 reads a periodically-refreshed table
    # (analytics.account_ring_score), never a live model_scoring.score() call per
    # transaction, because a GNN forward pass over the whole tenant graph does not fit
    # a per-batch budget the way a five-feature IsolationForest call does.
    "LAY-05": "graph-ring-score",
}

#: Every quantitative indicator must be in exactly one of these five sets. A rule in
#: none of them is silently unmeasured - it reports as dormant, which reads as "never
#: fired" rather than "cannot fire", and it inflates any coverage figure computed from
#: the catalogue. Asserted in the detection tests rather than trusted.
DECLARED = (set(COMPUTABLE) | set(NEEDS_REFERENCE_DATA)
            | set(NEEDS_EXTERNAL_DATA) | set(NEEDS_CBS_FEED) | set(NEEDS_LOS_FEED)
            | set(NEEDS_MODEL))



def load_context(db: Session, tenant_id: str, accounts: list[str],
                 now: datetime, *, window_hours: int = 24,
                 baseline_days: int = 30,
                 baseline_before: datetime | None = None) -> dict[str, AccountContext]:
    """One pass per aggregate for the whole batch, not one query per transaction.

    ``baseline_before`` excludes the batch under evaluation from the long baseline, so a
    burst is compared against normality rather than against itself.
    """
    ctx: dict[str, AccountContext] = {a: AccountContext() for a in accounts}
    if not accounts:
        return ctx

    since_w = now - timedelta(hours=window_hours)
    since_b = now - timedelta(days=baseline_days)
    cutoff = baseline_before or now
    p = {"t": tenant_id, "accts": accounts, "w": since_w, "b": since_b, "cut": cutoff}

    for r in db.execute(text(
        "SELECT debtor_account AS a, AVG(amount_paise) AS mean_amt "
        "FROM analytics.fact_transaction "
        "WHERE tenant_id = :t AND debtor_account = ANY(:accts) "
        "  AND ts >= :b AND ts < :cut "
        "GROUP BY debtor_account"), p).mappings():
        ctx[r["a"]].baseline_mean_paise = float(r["mean_amt"] or 0)

    for r in db.execute(text(
        "SELECT debtor_account AS a, "
        "       COALESCE(SUM(amount_paise), 0) AS out_paise, "
        "       COUNT(*) FILTER (WHERE amount_paise >= :ctr_lo AND amount_paise < :ctr) AS sub_ctr, "
        "       COUNT(*) FILTER (WHERE rail = 'UPI' AND amount_paise >= :upi_lo "
        "                        AND amount_paise < :upi) AS near_upi "
        "FROM analytics.fact_transaction "
        "WHERE tenant_id = :t AND debtor_account = ANY(:accts) AND ts >= :w "
        "GROUP BY debtor_account"),
        {**p, "ctr": CTR_PAISE, "ctr_lo": int(CTR_PAISE * 0.9),
         "upi": UPI_PINLESS_PAISE, "upi_lo": int(UPI_PINLESS_PAISE * 0.9)}).mappings():
        c = ctx[r["a"]]
        c.outflow_paise = int(r["out_paise"])
        c.sub_ctr_count = int(r["sub_ctr"])
        c.near_upi_limit_count = int(r["near_upi"])

    for r in db.execute(text(
        "SELECT creditor_account AS a, COALESCE(SUM(amount_paise), 0) AS in_paise, "
        "       COUNT(*) FILTER (WHERE amount_paise <= :small) AS small_credits "
        "FROM analytics.fact_transaction "
        "WHERE tenant_id = :t AND creditor_account = ANY(:accts) AND ts >= :w "
        "GROUP BY creditor_account"), {**p, "small": SMALL_CREDIT_PAISE}).mappings():
        c = ctx[r["a"]]
        c.inflow_paise = int(r["in_paise"])
        c.small_credit_count = int(r["small_credits"])

    # A hub is a hub whichever way the money flows. Counting only the accounts an
    # account *pays* misses fan-in entirely - which is the shape a mule collection
    # account actually has, and the shape LAY-02 is named for.
    for r in db.execute(text(
        "SELECT a, COUNT(DISTINCT cp) AS n FROM ("
        "  SELECT debtor_account AS a, creditor_account AS cp "
        "    FROM analytics.fact_transaction "
        "   WHERE tenant_id = :t AND debtor_account = ANY(:accts) AND ts >= :w "
        "  UNION ALL "
        "  SELECT creditor_account AS a, debtor_account AS cp "
        "    FROM analytics.fact_transaction "
        "   WHERE tenant_id = :t AND creditor_account = ANY(:accts) AND ts >= :w) x "
        "GROUP BY a"), p).mappings():
        ctx[r["a"]].distinct_counterparties = int(r["n"])

    # Dormancy is measured from the account's last activity on either side.
    for r in db.execute(text(
        "SELECT a, MAX(ts) AS last_ts FROM ("
        "  SELECT debtor_account AS a, ts FROM analytics.fact_transaction "
        "   WHERE tenant_id = :t AND debtor_account = ANY(:accts) "
        "  UNION ALL "
        "  SELECT creditor_account AS a, ts FROM analytics.fact_transaction "
        "   WHERE tenant_id = :t AND creditor_account = ANY(:accts)) x "
        "GROUP BY a"), p).mappings():
        ctx[r["a"]].last_seen = r["last_ts"]

    for r in db.execute(text(
        "SELECT debtor_account AS a, creditor_account AS cp, MIN(ts) AS first_ts "
        "FROM analytics.fact_transaction "
        "WHERE tenant_id = :t AND debtor_account = ANY(:accts) "
        "GROUP BY debtor_account, creditor_account"), p).mappings():
        c = ctx[r["a"]]
        c.known_counterparties.add(r["cp"])
        c.first_seen_pair[r["cp"]] = r["first_ts"]

    for r in db.execute(text(
        "SELECT debtor_account AS a, device_id FROM analytics.fact_transaction "
        "WHERE tenant_id = :t AND debtor_account = ANY(:accts) AND device_id <> '' "
        "GROUP BY debtor_account, device_id"), p).mappings():
        ctx[r["a"]].known_devices.add(r["device_id"])

    return ctx


def device_clusters(db: Session, tenant_id: str, device_ids: list[str],
                    now: datetime, *, days: int = 30) -> dict[str, int]:
    """How many distinct accounts share each device - LAY-04's observation."""
    if not device_ids:
        return {}
    rows = db.execute(text(
        "SELECT device_id, COUNT(DISTINCT debtor_account) AS n "
        "FROM analytics.fact_transaction "
        "WHERE tenant_id = :t AND device_id = ANY(:d) AND ts >= :since "
        "GROUP BY device_id"),
        {"t": tenant_id, "d": device_ids, "since": now - timedelta(days=days)}).mappings()
    return {r["device_id"]: int(r["n"]) for r in rows}




