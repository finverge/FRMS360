"""Generate synthetic FRMS/EWS data for the analytical store.

No data plane exists yet, so the dashboards are backed by generated traffic. Two things
matter about how it is generated:

1. **It is internally consistent.** Case values are DERIVED by summing the linked
   transactions rather than invented, because a generator that fabricates totals would
   make the reconciliation suite pass vacuously.

2. **It has real structure.** Transactions are drawn from a fixed account pool, so
   accounts recur and relationships exist. On top of that baseline the generator injects
   genuine typologies - fan-in/fan-out mule hubs, circular flows, rapid pass-through
   chains, trade-based patterns - and the LAY/TBM alerts are attached to *those* specific
   transactions. Without this the layering alerts are decoration: the Investigator asks
   "is this a ring?" and the underlying data has no rings in it.

Injected structures are grouped into a single case each, so drilling one alert reveals the
whole ring.

Usage:
    python scripts/generate_synthetic_data.py --reset            # rebuild everything
    python scripts/generate_synthetic_data.py --append --minutes 30   # add recent traffic
    python scripts/generate_synthetic_data.py --inject-breaks    # prove the checks fail
"""
import argparse
import os
import random
import sys
import uuid
from collections import defaultdict
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import delete, select  # noqa: E402

import httpx  # noqa: E402

from cp_common import SessionLocal, settings  # noqa: E402
from services.analytics_service.app.models import (  # noqa: E402
    CASE_STATES, FMR_CATEGORIES, FRAUD_STATES, PRODUCTS, REGIONS, SEGMENTS,
    FactAlert, FactCase, FactTransaction,
)
from services.tenant_service.app.models import Tenant  # noqa: E402

rnd = random.Random(20260728)  # deterministic: same data every run

RAIL_WEIGHTS = {"UPI": 0.55, "IMPS": 0.18, "NEFT": 0.15, "CARD": 0.09, "RTGS": 0.03}
RAIL_AMOUNT_RANGE = {  # rupees
    "UPI": (50, 60_000), "IMPS": (500, 400_000), "NEFT": (1_000, 2_000_000),
    "RTGS": (200_000, 50_000_000), "CARD": (100, 150_000),
}
FAMILY_RULES = {
    "VEL": ["VEL-01", "VEL-02", "VEL-03"], "SME": ["SME-01", "SME-02", "SME-03"],
    "BEH": ["BEH-01", "BEH-02", "BEH-03"], "LAY": ["LAY-01", "LAY-02", "LAY-03", "LAY-04"],
    "CPT": ["CPT-01", "CPT-02", "CPT-03"], "CHN": ["CHN-01", "CHN-02", "CHN-03"],
    "TBM": ["TBM-01", "TBM-02", "TBM-03"], "CBS": ["CBS-01", "CBS-02", "CBS-03"],
    "QUAL": ["QUAL-01", "QUAL-02", "QUAL-03"],
}
RAIL_FAMILIES = {
    "UPI": ["VEL", "SME", "BEH", "LAY", "CHN", "CPT"],
    "IMPS": ["VEL", "SME", "BEH", "LAY", "CHN", "CPT"],
    "NEFT": ["VEL", "SME", "BEH", "CPT", "CBS", "QUAL"],
    "RTGS": ["VEL", "CPT", "TBM", "CBS", "QUAL"],
    "CARD": ["CHN", "CPT", "LAY"],
}
ANALYSTS = ["a.rao", "s.iyer", "n.gupta", "p.menon"]

# The band each rule can match, mirroring the Tazama rule configs: which sub-rule fired,
# the human reason, and the observation vs threshold. This is what turns a score into an
# answer to "why was this flagged".
RULE_BANDS = {
    "LAY-01": (".03", "Outflow >= 90% of recent inflow drained within window",
               "outflow_ratio", 0.90, lambda: round(rnd.uniform(0.90, 0.999), 3)),
    "LAY-02": (".03", "40+ distinct counterparties in window (fan-in/fan-out hub)",
               "distinct_counterparties", 40, lambda: rnd.randint(40, 96)),
    "LAY-03": (".03", "Value returned to origin within 3 hops",
               "cycle_hops", 3, lambda: rnd.choice([2, 3])),
    "LAY-04": (".02", "4-9 accounts share a device fingerprint",
               "cluster_size", 4, lambda: rnd.randint(4, 9)),
    "VEL-01": (".02", "Transaction value 5x the 30-day baseline",
               "value_vs_baseline", 5.0, lambda: round(rnd.uniform(5, 22), 1)),
    "VEL-02": (".03", "High-value RTGS to a counterparty added in the last 24h",
               "beneficiary_age_hours", 24, lambda: rnd.randint(1, 23)),
    "VEL-03": (".02", "Burst of low-value credits followed by a single large debit",
               "credit_burst_count", 8, lambda: rnd.randint(8, 30)),
    "SME-01": (".03", "5+ transfers just below the CTR threshold in 24h",
               "sub_threshold_count", 5, lambda: rnd.randint(5, 14)),
    "SME-02": (".02", "Repeated UPI transfers just under the PIN-less limit",
               "near_limit_count", 4, lambda: rnd.randint(4, 11)),
    "SME-03": (".02", "One invoice split across many beneficiaries same day",
               "split_count", 6, lambda: rnd.randint(6, 18)),
    "BEH-01": (".03", "Dormant account transacting at high value",
               "dormancy_days", 180, lambda: rnd.randint(180, 900)),
    "BEH-02": (".02", "Borrowal account liquidated with funds from another bank",
               "external_funding_pct", 0.6, lambda: round(rnd.uniform(0.6, 1.0), 2)),
    "BEH-03": (".02", "Sale proceeds not routed through the lender",
               "unrouted_pct", 0.5, lambda: round(rnd.uniform(0.5, 0.95), 2)),
    "CPT-01": (".02", "High-value transfer to a beneficiary added <24h ago",
               "beneficiary_age_hours", 24, lambda: rnd.randint(1, 23)),
    "CPT-02": (".03", "Counterparty present on a negative / sanctions list",
               "list_match_score", 0.85, lambda: round(rnd.uniform(0.85, 1.0), 2)),
    "CPT-03": (".02", "Same collateral charged to multiple lenders",
               "lender_count", 2, lambda: rnd.randint(2, 5)),
    "CHN-01": (".03", "New device + new beneficiary + high value in one session",
               "risk_signals", 3, lambda: 3),
    "CHN-02": (".02", "Geo-velocity implies impossible travel",
               "implied_kmph", 900, lambda: rnd.randint(900, 4200)),
    "CHN-03": (".01", "Transaction outside the account's usual hours",
               "hour_deviation_sigma", 2.0, lambda: round(rnd.uniform(2.0, 5.5), 1)),
    "TBM-01": (".02", "Merchanting trade - import leg not disclosed",
               "undisclosed_legs", 1, lambda: rnd.randint(1, 3)),
    "TBM-02": (".02", "LC opened for local trade with a related party",
               "related_party_flag", 1, lambda: 1),
    "TBM-03": (".02", "Foreign bills outstanding beyond realisation norms",
               "days_outstanding", 270, lambda: rnd.randint(271, 700)),
    "CBS-01": (".03", "Loan funds routed to unrelated third parties",
               "diverted_pct", 0.4, lambda: round(rnd.uniform(0.4, 0.95), 2)),
    "CBS-02": (".02", "Heavy cash withdrawal in a loan account",
               "cash_ratio", 0.3, lambda: round(rnd.uniform(0.3, 0.8), 2)),
    "CBS-03": (".02", "Large transactions with inter-connected group companies",
               "group_exposure_pct", 0.35, lambda: round(rnd.uniform(0.35, 0.9), 2)),
    "QUAL-01": (".01", "Frequent change of primary banker",
                "banker_changes_24m", 2, lambda: rnd.randint(2, 6)),
    "QUAL-02": (".01", "Frequent ad-hoc / general-purpose loan requests",
                "adhoc_requests", 3, lambda: rnd.randint(3, 9)),
    "QUAL-03": (".02", "Material facts concealed, surfaced post-disbursement",
                "concealment_findings", 1, lambda: rnd.randint(1, 4)),
}


# How large an observation each rule plausibly produces. Deliberately INDEPENDENT of the
# configured threshold: if the range scaled with the threshold, retuning a rule would
# scale the observations too and nothing would change. Fixed ranges mean a lower
# threshold genuinely catches more traffic - which is the whole point of making the
# control plane authoritative.
OBSERVATION_RANGE = {
    "LAY-01": (0.30, 1.00), "LAY-02": (4, 120), "LAY-03": (2, 9), "LAY-04": (1, 14),
    "VEL-01": (0.8, 25.0), "VEL-02": (1, 96), "VEL-03": (1, 40),
    "SME-01": (1, 18), "SME-02": (1, 14), "SME-03": (1, 24),
    "BEH-01": (10, 1000), "BEH-02": (0.1, 1.0), "BEH-03": (0.1, 1.0),
    "CPT-01": (1, 96), "CPT-02": (0.2, 1.0), "CPT-03": (1, 6),
    "CHN-01": (1, 4), "CHN-02": (50, 5000), "CHN-03": (0.3, 6.0),
    "TBM-01": (0, 4), "TBM-02": (0, 1), "TBM-03": (30, 800),
    "CBS-01": (0.05, 1.0), "CBS-02": (0.05, 0.9), "CBS-03": (0.05, 1.0),
    "QUAL-01": (0, 7), "QUAL-02": (0, 11), "QUAL-03": (0, 5),
}

def load_tenant_rules(tenant_id: str) -> tuple[dict, bool]:
    """The tenant's ACTIVE rule catalogue from the control plane.

    Returns ``(rules, from_control_plane)``. Detection reading this is what makes the
    console authoritative: retune LAY-02 for one bank and only that bank detects more.
    """
    try:
        r = httpx.get(f"{settings.config_service_url}/internal/rules/{tenant_id}",
                      headers={"x-internal-key": settings.internal_api_key}, timeout=8.0)
        r.raise_for_status()
        rules = r.json().get("rules", {})
        if rules:
            return rules, True
    except Exception as exc:  # noqa: BLE001
        print(f"      WARNING control plane unreachable ({exc}); using built-in defaults")
    return {rid: {"rule_id": rid,
                  "family": rid.split("-")[0],
                  "sub_rule_ref": band[0],
                  "reason": band[1],
                  "observed_unit": band[2],
                  "threshold": float(band[3]),
                  "qualitative": rid.startswith("QUAL")}
            for rid, band in RULE_BANDS.items()}, False


def load_tenant_policy(tenant_id: str) -> tuple[dict, bool]:
    """Severity cut-offs come from the tenant's board-approved policy."""
    try:
        r = httpx.get(f"{settings.config_service_url}/internal/policy/{tenant_id}",
                      headers={"x-internal-key": settings.internal_api_key}, timeout=8.0)
        r.raise_for_status()
        j = r.json()
        return (j.get("body", {}) or {}).get("thresholds", {}) or {}, bool(j.get("available"))
    except Exception:  # noqa: BLE001
        return {}, False


def observe(rule_id: str, strong: bool = False) -> float:
    """Draw a plausible observation for a rule.

    strong narrows to the top of the range, for transactions that are part of an
    injected typology - genuine fraud produces a pronounced signal.
    """
    lo, hi = OBSERVATION_RANGE.get(rule_id, (0.0, 1.0))
    if strong:
        lo = lo + (hi - lo) * 0.72
    value = rnd.uniform(lo, hi)
    return round(value, 3) if isinstance(lo, float) or isinstance(hi, float) else float(round(value))


def evaluate(rule_id: str, observed: float, catalogue: dict) -> dict | None:
    """Fire only when the observation crosses the tenant's CONFIGURED threshold."""
    rule = catalogue.get(rule_id)
    if not rule or rule.get("threshold") is None:
        return None
    threshold = float(rule["threshold"])
    if observed < threshold:
        return None
    return {
        "sub_rule_ref": rule.get("sub_rule_ref") or ".03",
        "matched_reason": rule.get("reason") or "Threshold exceeded",
        "observed_value": float(observed),
        "threshold_value": threshold,
        "observed_unit": rule.get("observed_unit") or "",
    }


def severity_for(score: float, policy: dict) -> str:
    crit = policy.get("severity_critical_score", 380)
    high = policy.get("severity_high_score", 260)
    med = policy.get("severity_medium_score", 150)
    return ("critical" if score >= crit else "high" if score >= high
            else "medium" if score >= med else "low")


def _trace(rule: str) -> dict:
    """Produce the evaluation trace for a rule, or a neutral one if unmapped."""
    band = RULE_BANDS.get(rule)
    if not band:
        return {"sub_rule_ref": ".01", "matched_reason": "Threshold exceeded",
                "observed_value": None, "threshold_value": None, "observed_unit": ""}
    ref, reason, unit, threshold, sample = band
    return {"sub_rule_ref": ref, "matched_reason": reason,
            "observed_value": float(sample()), "threshold_value": float(threshold),
            "observed_unit": unit}
ACCOUNT_POOL_SIZE = 1400   # accounts recur, so relationships exist


def _acct() -> str:
    return f"AC{rnd.randint(10**9, 10**10 - 1)}"


def _weighted_rail() -> str:
    r, acc = rnd.random(), 0.0
    for rail, w in RAIL_WEIGHTS.items():
        acc += w
        if r <= acc:
            return rail
    return "UPI"


class Builder:
    """Accumulates transactions and the typology tags attached to them."""

    def __init__(self, tenant_id: str, pool: list[str]):
        self.tenant_id = tenant_id
        self.pool = pool
        self.txns: list[FactTransaction] = []
        # txn_id -> (family, rule, ring_id, score_floor)
        self.tagged: dict[str, tuple[str, str, str, float]] = {}

    def txn(self, ts, rail, amount_paise, debtor=None, creditor=None,
            device=None, product=None, status="settled") -> FactTransaction:
        t = FactTransaction(
            source="synthetic",
            txn_id=f"T{uuid.uuid4().hex[:16]}", tenant_id=self.tenant_id, ts=ts, rail=rail,
            amount_paise=amount_paise,
            debtor_account=debtor or rnd.choice(self.pool),
            creditor_account=creditor or rnd.choice(self.pool),
            branch=f"BR{rnd.randint(1, 240):04d}", region=rnd.choice(REGIONS),
            product=product or rnd.choice(PRODUCTS), customer_segment=rnd.choice(SEGMENTS),
            channel=rnd.choice(["mobile", "internet", "branch", "atm", "pos"]),
            device_id=device or f"D{rnd.randint(10**7, 10**8 - 1)}",
            ip_addr=f"{rnd.randint(10,223)}.{rnd.randint(0,255)}."
                    f"{rnd.randint(0,255)}.{rnd.randint(1,254)}",
            status=status,
        )
        self.txns.append(t)
        return t

    def tag(self, t: FactTransaction, family: str, rule: str, ring: str, score: float) -> None:
        self.tagged[t.txn_id] = (family, rule, ring, score)


# ---------------- typology injectors ----------------
def inject_fan_in_out(b: Builder, start: datetime, days: int, count: int) -> None:
    """A mule hub: many sources pay in, the hub pays out to many destinations shortly
    after. This is what makes LAY-02 (fan-in/fan-out) a real finding rather than a label."""
    for n in range(count):
        ring = f"RING-FANIO-{n:02d}"
        hub = rnd.choice(b.pool)
        sources = rnd.sample(b.pool, k=rnd.randint(8, 16))
        sinks = rnd.sample(b.pool, k=rnd.randint(6, 12))
        t0 = start + timedelta(seconds=rnd.randint(0, days * 86400 - 7200))
        device = f"D{rnd.randint(10**7, 10**8 - 1)}"     # shared device across the ring
        collected = 0
        for i, src in enumerate(sources):
            amt = rnd.randint(9_000, 49_000) * 100
            collected += amt
            t = b.txn(t0 + timedelta(minutes=i * rnd.randint(1, 4)),
                      rnd.choice(["UPI", "IMPS"]), amt, debtor=src, creditor=hub, device=device)
            b.tag(t, "LAY", "LAY-02", ring, rnd.uniform(300, 420))
        # hub drains to the sinks within the hour -> also a pass-through signature
        for i, dst in enumerate(sinks):
            amt = int(collected / len(sinks) * rnd.uniform(0.85, 1.0))
            t = b.txn(t0 + timedelta(minutes=90 + i * rnd.randint(1, 3)),
                      rnd.choice(["UPI", "IMPS"]), amt, debtor=hub, creditor=dst, device=device)
            b.tag(t, "LAY", "LAY-01", ring, rnd.uniform(320, 460))


def inject_circular(b: Builder, start: datetime, days: int, count: int) -> None:
    """A -> B -> C -> A within a day: value returns to origin (LAY-03)."""
    for n in range(count):
        ring = f"RING-CIRC-{n:02d}"
        hops = rnd.sample(b.pool, k=rnd.choice([3, 4, 5]))
        amt = rnd.randint(200_000, 2_000_000) * 100
        t0 = start + timedelta(seconds=rnd.randint(0, days * 86400 - 86400))
        chain = hops + [hops[0]]
        for i in range(len(chain) - 1):
            t = b.txn(t0 + timedelta(hours=i * rnd.randint(1, 5)),
                      rnd.choice(["NEFT", "IMPS", "RTGS"]),
                      int(amt * rnd.uniform(0.93, 0.99)),
                      debtor=chain[i], creditor=chain[i + 1])
            b.tag(t, "LAY", "LAY-03", ring, rnd.uniform(340, 480))


def inject_pass_through(b: Builder, start: datetime, days: int, count: int) -> None:
    """Credit in, near-identical debit out within minutes (LAY-01)."""
    for n in range(count):
        ring = f"RING-PASS-{n:02d}"
        mule = rnd.choice(b.pool)
        amt = rnd.randint(50_000, 900_000) * 100
        t0 = start + timedelta(seconds=rnd.randint(0, days * 86400 - 3600))
        t_in = b.txn(t0, "IMPS", amt, creditor=mule)
        t_out = b.txn(t0 + timedelta(minutes=rnd.randint(2, 25)), "IMPS",
                      int(amt * rnd.uniform(0.95, 0.995)), debtor=mule)
        for t in (t_in, t_out):
            b.tag(t, "LAY", "LAY-01", ring, rnd.uniform(300, 440))


def inject_trade_based(b: Builder, start: datetime, days: int, count: int) -> None:
    """High-value RTGS trade-finance flows - the TBM family, which was barely
    represented before (5 alerts across three tenants)."""
    for n in range(count):
        ring = f"TBM-{n:02d}"
        rule = rnd.choice(FAMILY_RULES["TBM"])
        for _ in range(rnd.randint(1, 3)):
            t = b.txn(start + timedelta(seconds=rnd.randint(0, days * 86400)),
                      "RTGS", rnd.randint(2_000_000, 60_000_000) * 100,
                      product="trade_finance")
            b.tag(t, "TBM", rule, ring, rnd.uniform(260, 430))


def inject_burst(b: Builder, start: datetime, days: int) -> tuple[datetime, datetime]:
    """A concentrated attack window. Real fraud is bursty; uniform noise makes the trend
    line look flat and the EWS dashboard uninteresting."""
    burst_start = start + timedelta(days=int(days * 0.62))
    burst_end = burst_start + timedelta(days=3)
    for _ in range(700):
        rail = rnd.choice(["UPI", "IMPS", "UPI", "CARD"])
        lo, hi = RAIL_AMOUNT_RANGE[rail]
        t = b.txn(burst_start + timedelta(seconds=rnd.randint(0, 3 * 86400)),
                  rail, rnd.randint(lo, hi) * 100)
        if rnd.random() < 0.35:
            fam = rnd.choice(["VEL", "CHN", "SME"])
            b.tag(t, fam, rnd.choice(FAMILY_RULES[fam]), "BURST", rnd.uniform(280, 470))
    return burst_start, burst_end


def generate_for_tenant(db, tenant_id: str, days: int, n_txns: int) -> dict:
    catalogue, from_cp = load_tenant_rules(tenant_id)
    policy, policy_cp = load_tenant_policy(tenant_id)
    now = datetime.now(timezone.utc)
    start = now - timedelta(days=days)
    pool = [_acct() for _ in range(ACCOUNT_POOL_SIZE)]
    b = Builder(tenant_id, pool)

    # 1) baseline traffic
    for _ in range(n_txns):
        rail = _weighted_rail()
        lo, hi = RAIL_AMOUNT_RANGE[rail]
        b.txn(start + timedelta(seconds=rnd.randint(0, days * 86400)),
              rail, rnd.randint(lo, hi) * 100)

    # 2) injected structure
    inject_fan_in_out(b, start, days, count=6)
    inject_circular(b, start, days, count=8)
    inject_pass_through(b, start, days, count=14)
    inject_trade_based(b, start, days, count=18)
    burst = inject_burst(b, start, days)

    db.add_all(b.txns)
    db.flush()

    # 3) alerts - forced on tagged transactions, random on the rest
    txn_by_id = {t.txn_id: t for t in b.txns}
    alerts: list[FactAlert] = []
    ring_of: dict[str, str] = {}

    def make_alert(t, family, rule, score, ring=None, force=False):
        """Evaluate the transaction against the tenant's CONFIGURED rule.

        ``force`` is used for injected typologies: they are real fraud, so their
        observation is drawn above the configured threshold rather than at random.
        """
        # Injected typologies are real fraud, so they present a STRONG observation -
        # drawn from the top of the rule's range. They are not force-fired past the
        # threshold, because a tenant that sets a threshold too high genuinely should
        # miss real fraud; hiding that would make misconfiguration invisible.
        observed = observe(rule, strong=force)
        trace = evaluate(rule, observed, catalogue)
        if trace is None:
            return None          # below this tenant's configured threshold - no alert
        severity = severity_for(score, policy)
        a_ts = t.ts + timedelta(seconds=rnd.randint(1, 20))
        # Injected typologies are real fraud, so they resolve true-positive far more often.
        tp_rate = 0.72 if ring else 0.34
        if rnd.random() < 0.78:
            disposition = "true_positive" if rnd.random() < tp_rate else "false_positive"
            first_touch = a_ts + timedelta(minutes=rnd.randint(2, 240))
            disp_ts = first_touch + timedelta(minutes=rnd.randint(5, 900))
        else:
            disposition, first_touch, disp_ts = "pending", None, None
            if rnd.random() < 0.5:
                first_touch = a_ts + timedelta(minutes=rnd.randint(2, 120))
        a = FactAlert(
            source="synthetic",
            alert_id=f"A{uuid.uuid4().hex[:16]}", tenant_id=tenant_id, txn_id=t.txn_id,
            ts=a_ts, rule_family=family, rule_id=rule,
            typology="mule-layering" if family == "LAY" else f"{family.lower()}-typology",
            score=round(score, 1), severity=severity, disposition=disposition, case_id=None,
            analyst=rnd.choice(ANALYSTS + [""]),
            first_touch_ts=first_touch, disposition_ts=disp_ts,
            config_version=rnd.choice(["1.0.0", "1.0.0", "1.1.0"]),
            **trace,
        )
        alerts.append(a)
        if ring:
            ring_of[a.alert_id] = ring
        return a

    for txn_id, (fam, rule, ring, score) in b.tagged.items():
        make_alert(txn_by_id[txn_id], fam, rule, score, ring=ring, force=True)

    untagged = [t for t in b.txns if t.txn_id not in b.tagged]
    for t in rnd.sample(untagged, k=max(1, int(len(untagged) * 0.03))):
        fam = rnd.choice(RAIL_FAMILIES[t.rail])
        make_alert(t, fam, rnd.choice(FAMILY_RULES[fam]), rnd.uniform(60, 380))

    db.add_all(alerts)
    db.flush()

    # 4) cases - one per injected ring (so a case IS the ring), plus small ad-hoc groups
    cases: list[FactCase] = []
    by_ring: dict[str, list[FactAlert]] = defaultdict(list)
    loose: list[FactAlert] = []
    for a in alerts:
        if a.disposition != "true_positive":
            continue
        r = ring_of.get(a.alert_id)
        (by_ring[r].append(a) if r and r != "BURST" else loose.append(a))

    groups = list(by_ring.values())
    rnd.shuffle(loose)
    i = 0
    while i < len(loose):
        groups.append(loose[i:i + rnd.choice([1, 1, 1, 2, 3])])
        i += len(groups[-1])

    for group in groups:
        if not group:
            continue
        case_id = f"C{uuid.uuid4().hex[:16]}"
        opened = min(a.ts for a in group) + timedelta(minutes=rnd.randint(10, 600))
        # Derived, never invented - this is what makes invariant R-03 meaningful.
        amount_paise = sum(txn_by_id[a.txn_id].amount_paise for a in group)

        state = rnd.choices(CASE_STATES, weights=[14, 16, 12, 8, 10, 12, 16, 12])[0]
        is_fraud = state in FRAUD_STATES
        rfa = state in ("rfa_flagged", "natural_justice", "response_evaluation") or is_fraud

        show_cause = resp_due = decision = None
        fmr_due = fmr_filed = str_due = str_filed = closed = None
        if rfa:
            show_cause = opened + timedelta(days=rnd.randint(1, 6))
            resp_due = show_cause + timedelta(days=21)
        if state == "response_evaluation" or is_fraud:
            decision = (resp_due + timedelta(days=rnd.randint(1, 9)) if rnd.random() < 0.12
                        else show_cause + timedelta(days=rnd.randint(7, 20)))
        if is_fraud:
            fmr_due = decision + timedelta(days=7)
            if state in ("fmr_reported", "closed_fraud") or rnd.random() < 0.55:
                fmr_filed = fmr_due - timedelta(days=rnd.randint(0, 5))
            if rnd.random() < 0.45:
                str_due = decision + timedelta(days=7)
                if rnd.random() < 0.7:
                    str_filed = str_due - timedelta(days=rnd.randint(0, 4))
        if state in ("closed_fraud", "exonerated"):
            closed = (decision or opened) + timedelta(days=rnd.randint(1, 30))

        recovered = (int(amount_paise * rnd.uniform(0, 0.45))
                     if is_fraud and rnd.random() < 0.5 else 0)
        head = txn_by_id[group[0].txn_id]
        cases.append(FactCase(
            source="synthetic",
            case_id=case_id, tenant_id=tenant_id, opened_ts=opened, state=state,
            severity=max(group, key=lambda a: a.score).severity,
            fmr_category=rnd.choice(FMR_CATEGORIES),
            amount_paise=amount_paise, recovered_paise=recovered, rfa_flag=rfa,
            rail=head.rail, region=head.region, product=head.product,
            customer_segment=head.customer_segment, assignee=rnd.choice(ANALYSTS),
            show_cause_ts=show_cause, response_due_ts=resp_due, decision_ts=decision,
            fmr_due_ts=fmr_due, fmr_filed_ts=fmr_filed,
            str_due_ts=str_due, str_filed_ts=str_filed, closed_ts=closed,
        ))
        for a in group:
            a.case_id = case_id
    db.add_all(cases)

    # a slice of alerted traffic was stopped in flight -> 'prevented value'
    alerted_txns = [txn_by_id[a.txn_id] for a in alerts]
    for t in rnd.sample(alerted_txns, k=max(1, int(len(alerted_txns) * 0.18))):
        t.status = "interdicted"

    db.commit()
    return {
        "transactions": len(b.txns), "alerts": len(alerts), "cases": len(cases),
        "rings": len(by_ring), "burst": burst[0].date().isoformat(),
        "source": "control-plane" if from_cp else "built-in defaults",
        "rules": len(catalogue),
    }


def append_recent(db, tenant_id: str, minutes: int, rate_per_min: int = 45) -> dict:
    """Append traffic ending now, so the Real-Time view moves and ingestion lag falls."""
    now = datetime.now(timezone.utc)
    pool = [r[0] for r in db.execute(
        select(FactTransaction.debtor_account)
        .where(FactTransaction.tenant_id == tenant_id).limit(400))] or [_acct() for _ in range(50)]
    b = Builder(tenant_id, pool)
    # Throughput is the demo dial: too low and the Real-Time view looks dead.
    n = max(10, int(minutes * rate_per_min))
    for _ in range(n):
        rail = _weighted_rail()
        lo, hi = RAIL_AMOUNT_RANGE[rail]
        b.txn(now - timedelta(seconds=rnd.randint(0, minutes * 60)),
              rail, rnd.randint(lo, hi) * 100)
    db.add_all(b.txns)
    db.flush()

    # Live traffic is scored exactly like the historical load: against the tenant's
    # configured thresholds. Using hard-coded bands here would mean the feed quietly
    # ignored the control plane, so a retuned rule would apply to backfilled data but
    # not to anything arriving now.
    catalogue, _ = load_tenant_rules(tenant_id)
    policy, _ = load_tenant_policy(tenant_id)

    alerts = []
    for t in rnd.sample(b.txns, k=max(1, int(len(b.txns) * 0.06))):
        fam = rnd.choice(RAIL_FAMILIES[t.rail])
        rule_id_choice = rnd.choice(FAMILY_RULES[fam])
        trace = evaluate(rule_id_choice, observe(rule_id_choice), catalogue)
        if trace is None:
            continue                      # below this tenant's threshold
        score = rnd.uniform(80, 460)
        alerts.append(FactAlert(
            alert_id=f"A{uuid.uuid4().hex[:16]}", tenant_id=tenant_id, txn_id=t.txn_id,
            ts=t.ts + timedelta(seconds=rnd.randint(1, 20)), rule_family=fam,
            rule_id=rule_id_choice, typology=f"{fam.lower()}-typology",
            score=round(score, 1), severity=severity_for(score, policy),
            disposition="pending", config_version="1.1.0",
            **trace,
        ))
    db.add_all(alerts)
    db.commit()
    return {"transactions": len(b.txns), "alerts": len(alerts)}


def inject_breaks(db, tenant_id: str) -> list[str]:
    """Deliberately corrupt data so the reconciliation checks have something to catch."""
    broken = []
    case = db.scalar(select(FactCase).where(FactCase.tenant_id == tenant_id).limit(1))
    if case:
        case.amount_paise += 123_45
        broken.append(f"R-03: inflated {case.case_id} by Rs 123.45")
    db.add(FactAlert(
        alert_id=f"A{uuid.uuid4().hex[:16]}", tenant_id=tenant_id,
        txn_id="T_DOES_NOT_EXIST", ts=datetime.now(timezone.utc), rule_family="VEL",
        rule_id="VEL-01", typology="vel-typology", score=99.0, severity="low",
        disposition="pending"))
    broken.append("R-01: added an alert pointing at a missing transaction")
    over = db.scalar(select(FactCase).where(FactCase.tenant_id == tenant_id).offset(1).limit(1))
    if over:
        over.recovered_paise = over.amount_paise + 1
        broken.append(f"R-09: recovery exceeds value on {over.case_id}")
    db.commit()
    return broken


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=60)
    ap.add_argument("--txns", type=int, default=9000, help="baseline transactions per tenant")
    ap.add_argument("--reset", action="store_true")
    ap.add_argument("--append", action="store_true", help="add traffic ending now")
    ap.add_argument("--minutes", type=int, default=30, help="window for --append")
    ap.add_argument("--inject-breaks", action="store_true")
    ap.add_argument("--tenant", default=None)
    args = ap.parse_args()

    db = SessionLocal()
    try:
        tenants = [t for t in db.scalars(select(Tenant))
                   if not args.tenant or t.slug == args.tenant]
        if not tenants:
            print("No tenants found. Run scripts/api_seed.py first.")
            return

        if args.reset:
            for model in (FactAlert, FactCase, FactTransaction):
                db.execute(delete(model))
            db.commit()
            print("cleared existing analytical data")

        for t in tenants:
            if args.append:
                s = append_recent(db, t.id, args.minutes)
                print(f"  {t.slug:<12} +{s['transactions']} txns, +{s['alerts']} alerts (last {args.minutes}m)")
                continue
            s = generate_for_tenant(db, t.id, args.days, args.txns)
            print(f"  {t.slug:<12} txns={s['transactions']:>6} alerts={s['alerts']:>5} "
                  f"cases={s['cases']:>4} rings={s['rings']:>3} "
                  f"rules={s['rules']:>3} config={s['source']}")
            if args.inject_breaks:
                for line in inject_breaks(db, t.id):
                    print(f"      BREAK {line}")
        print("\ndone.")
    finally:
        db.close()


if __name__ == "__main__":
    main()
