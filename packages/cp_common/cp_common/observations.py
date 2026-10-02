"""What each indicator measures, shared by every lane that scores.

This module holds the *measurement* semantics: given an account's context and one
transaction, what is the observed value for each indicator. It deliberately does not hold
thresholds — those live in the tenant's catalogue, so a bank can retune a band without
touching this — and it deliberately does not query anything.

**Why it is in cp_common rather than in the detection package.** Two lanes now score
transactions: the near-real-time engine, which builds context with SQL, and the inline
decision lane, which rebuilds the same context from precomputed counters. They must agree
about what an observation *means*, and the only reliable way to guarantee that is for
both to call this function. A second implementation would drift, and because the lanes
see different traffic, the drift would be invisible until a bank asked why the same
payment scored differently in each.

The measurement is separated from the comparison for the same reason the catalogue owns
the comparator: an observation must not depend on which way the rule compares it. See
LAY-03 below, where emitting a zero would fire the rule on every account in the bank.
"""
from dataclasses import dataclass, field
from datetime import datetime


#: The CTR reporting threshold. Structuring is measured as approaching it from below.
CTR_PAISE = 10_00_000 * 100          # Rs 10 lakh
#: UPI PIN-less / low-value ceiling that SME-02 watches transfers cluster under.
UPI_PINLESS_PAISE = 5_000 * 100
#: What counts as "high value" for the counterparty and channel rules, in the absence of
#: a per-customer limit. A tenant-level override belongs in FRM policy eventually.
HIGH_VALUE_PAISE = 5_00_000 * 100    # Rs 5 lakh
#: A "small credit" for the burst rule.
SMALL_CREDIT_PAISE = 50_000 * 100


@dataclass
class AccountContext:
    """Everything measured once per account, reused for each of its transactions."""
    baseline_mean_paise: float = 0.0
    inflow_paise: int = 0
    outflow_paise: int = 0
    distinct_counterparties: int = 0
    small_credit_count: int = 0
    sub_ctr_count: int = 0
    near_upi_limit_count: int = 0
    last_seen: datetime | None = None
    known_devices: set[str] = field(default_factory=set)
    known_counterparties: set[str] = field(default_factory=set)
    first_seen_pair: dict[str, datetime] = field(default_factory=dict)
    device_cluster_size: int = 0



def _aware(ts, ref):
    if ts is None:
        return None
    return ts if ts.tzinfo else ts.replace(tzinfo=ref.tzinfo)



#: Rules that describe the *receiving* account rather than the paying one. A mule
#: collection account may never pay anybody - it exists to be paid - so evaluating only
#: from the debtor's side means the hub of a fan-in is never scored at all. These are
#: measured a second time with the creditor as the subject.
CREDITOR_SIDE = ("LAY-01", "LAY-02", "BEH-01", "VEL-03")


def hour_deviation(profile: tuple[float, float, int] | None, ts: datetime) -> float | None:
    """How many standard deviations from the account's usual hour, or None if unknown."""
    if profile is None:
        return None
    mean_hour, sd_hours, _n = profile
    hour = ts.hour + ts.minute / 60.0
    diff = abs(hour - mean_hour)
    diff = min(diff, 24.0 - diff)          # circular distance
    if sd_hours <= 0.25:
        # A perfectly regular account: any material departure is significant, but the
        # sigma would divide by ~zero and report absurd values. Floored deliberately.
        sd_hours = 0.25
    return round(diff / sd_hours, 4)


def observe(txn: dict, ctx: AccountContext, clusters: dict[str, int],
            now: datetime, *, subject: str = "debtor",
            cycles: dict[str, int] | None = None,
            hour_profile=None, screening: dict | None = None) -> dict[str, float]:
    """The observation for each computable indicator, keyed by rule id.

    ``subject`` says whose behaviour is being described. Most indicators are about the
    account sending money; a fan-in hub is about the account receiving it, and scoring
    only the payer would miss the one account that matters.

    A rule absent from the result was not measurable for this transaction - which is not
    the same as measuring zero, and must not be conflated with it.
    """
    out: dict[str, float] = {}
    account = txn["creditor_account"] if subject == "creditor" else txn["debtor_account"]

    # Round-tripping is a property of the account either way round.
    #
    # Emitted only when a cycle actually exists. "No cycle" is not "a cycle of length
    # zero": the catalogue reads "value returned to origin *within* 3 hops", and under an
    # lte comparator an observation of 0 would fire this rule on every account in the
    # bank that has no circular flow at all. Leaving it unmeasured is correct under either
    # comparator, which is the point - the rule owns the threshold, this owns the
    # measurement, and the measurement must not depend on which way the rule compares.
    if cycles is not None:
        hops = cycles.get(account)
        if hops:
            out["LAY-03"] = float(hops)

    if subject == "creditor":
        # From the receiving account's point of view: is it collecting from an
        # implausible number of sources, and is it passing the money straight on?
        if ctx.inflow_paise > 0:
            out["LAY-01"] = ctx.outflow_paise / ctx.inflow_paise
        out["LAY-02"] = float(ctx.distinct_counterparties)
        last = _aware(ctx.last_seen, txn["ts"])
        if last is not None and int(txn["amount_paise"]) >= HIGH_VALUE_PAISE:
            out["BEH-01"] = max(0.0, (txn["ts"] - last).total_seconds() / 86400.0)
        if ctx.small_credit_count:
            out["VEL-03"] = float(ctx.small_credit_count)
        return out

    amount = int(txn["amount_paise"])
    cp = txn["creditor_account"]
    ts = txn["ts"]

    if ctx.baseline_mean_paise > 0:
        out["VEL-01"] = amount / ctx.baseline_mean_paise

    # Beneficiary age drives two rules: any high-value transfer (CPT-01) and RTGS
    # specifically (VEL-02). Both are "lte" rules, so the observation is simply the age
    # in hours - no inversion, no cleverness. An unseen counterparty is age zero.
    if amount >= HIGH_VALUE_PAISE:
        first = _aware(ctx.first_seen_pair.get(cp), ts)
        age_hours = 0.0 if first is None else max(
            0.0, (ts - first).total_seconds() / 3600.0)
        out["CPT-01"] = age_hours
        if txn["rail"] == "RTGS":
            out["VEL-02"] = age_hours

    if amount >= HIGH_VALUE_PAISE and ctx.small_credit_count:
        out["VEL-03"] = float(ctx.small_credit_count)

    out["SME-01"] = float(ctx.sub_ctr_count)
    if txn["rail"] == "UPI":
        out["SME-02"] = float(ctx.near_upi_limit_count)

    last = _aware(ctx.last_seen, ts)
    if last is not None:
        gap_days = (ts - last).total_seconds() / 86400.0
        if amount >= HIGH_VALUE_PAISE:
            out["BEH-01"] = max(0.0, gap_days)

    # The batch is already projected, so these aggregates include this transaction.
    # Adding it again would inflate every observation by one row.
    if ctx.inflow_paise > 0:
        out["LAY-01"] = ctx.outflow_paise / ctx.inflow_paise
    out["LAY-02"] = float(ctx.distinct_counterparties)

    device = txn.get("device_id") or ""
    if device:
        n = clusters.get(device, 0)
        if n:
            out["LAY-04"] = float(n)

    signals = 0
    if device and device not in ctx.known_devices:
        signals += 1
    if cp not in ctx.known_counterparties:
        signals += 1
    if amount >= HIGH_VALUE_PAISE:
        signals += 1
    out["CHN-01"] = float(signals)

    # AI/ML roadmap Phase 1 (device & behavioural biometrics). Both scores are supplied
    # by an integrated provider, not computed here - absent on a transaction is the
    # normal case today (no tenant has this integration wired up yet; see
    # features.NEEDS_EXTERNAL_DATA), and must stay unmeasured rather than read as zero
    # risk. A transaction that does carry them is scored exactly like every other
    # indicator, no separate code path.
    device_risk = txn.get("device_risk_score")
    if device_risk is not None:
        out["CHN-04"] = float(device_risk)
    behavior_anomaly = txn.get("behavior_anomaly_score")
    if behavior_anomaly is not None:
        out["CHN-05"] = float(behavior_anomaly)

    # Outside the account's usual hours. Absent when the account has too little history
    # to have a usual hour - which is not the same as being unremarkable.
    if hour_profile is not None:

        sigma = hour_deviation(hour_profile, ts)
        if sigma is not None:
            out["CHN-03"] = sigma

    # Sanctions / negative-list screening. ``screen_name`` returns None when no list is
    # loaded, and the indicator is then left unmeasured on purpose.
    if screening is not None:
        # Imported lazily and by absolute path: the screening helpers read reference
        # lists, which belong to analytics. cp_common must not depend on a service, so
        # this is the one place the dependency is inverted at call time rather than
        # declared at import time. A caller with no screening set passes None and the
        # indicator is left unmeasured, which is the correct behaviour anyway.
        try:
            from services.analytics_service.app.detection.reference import (
                screen_collateral, screen_name, screen_title_dispute,
            )
        except ImportError:
            # A deployment carrying only the decision lane has no reference module. The
            # screening indicators are then left unmeasured, which is the same outcome as
            # an unloaded list - and the correct one. Crashing here would take down a
            # service that sits in the payment path.
            return out

        hit = screen_name(screening, cp)
        if hit is not None:
            out["CPT-02"] = hit[0]
        asset = txn.get("collateral_ref") or ""
        if asset:
            charge = screen_collateral(screening, asset)
            if charge is not None:
                out["CPT-03"] = charge[0]
            dispute = screen_title_dispute(screening, asset)
            if dispute is not None:
                out["CPT-04"] = dispute[0]

    return out
