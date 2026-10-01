"""The EWS rule catalogue - the control plane's authoritative rule definitions.

This is deliberately the *only* place a rule's identity and thresholds are defined. The
catalogue previously lived in the data generator, which meant the control plane stored
Tazama configs nobody consumed (rule ``018@1.0.0``) while detection used a different
vocabulary entirely (``LAY-02``). Changing a threshold in the console changed nothing.

Each rule is stored as one versioned config of kind ``rule``, in Tazama's band shape:
a band declares the observation range it matches, the reason text an analyst sees, and
the sub-rule reference recorded on the alert. Detection reads these thresholds, so
retuning a band in the console genuinely changes what fires.

Mapped to nine RBI EWS indicator families, plus a tenth (BR-214):
VEL velocity · SME structuring · BEH behaviour · LAY layering/mule · CPT counterparty
CHN channel/device · TBM trade-based · CBS loan-account misuse · QUAL qualitative
LOS loan origination - falsified applications, straw borrowers, collusive valuations,
none of which appear on a payment rail or a disbursed loan account, because they happen
*before* either exists. This is a genuine addition to the "nine families" framing
elsewhere in the documentation, not an error in it - see BR-214 in the BRD for why.
"""

# (rule_id, family, description, observed unit, threshold, band ref, reason)
_RULES = [
    ("VEL-01", "VEL", "Transaction value against the account's 30-day baseline",
     "value_vs_baseline", 5.0, ".02", "Transaction value 5x the 30-day baseline"),
    ("VEL-02", "VEL", "High-value RTGS to a newly added counterparty",
     "beneficiary_age_hours", 24, ".03", "High-value RTGS to a counterparty added in the last 24h"),
    ("VEL-03", "VEL", "Burst of low-value credits followed by a large debit",
     "credit_burst_count", 8, ".02", "Burst of low-value credits followed by a single large debit"),
    # AI/ML roadmap Phase 2 (BRD OD-06/s19; HLD AD-14/s16). Scored by the tenant's active
    # `model` config version, not a fixed formula - measured only while that version is
    # active, unmeasurable otherwise. See detection/model_scoring.py.
    ("VEL-04", "VEL", "ML-scored transaction velocity/anomaly, above threshold",
     "anomaly_score", 0.7, ".02", "Model-scored anomaly above threshold "
     "(velocity, counterparty and outflow features taken together)"),

    ("SME-01", "SME", "Transfers just below the CTR reporting threshold",
     "sub_threshold_count", 5, ".03", "5+ transfers just below the CTR threshold in 24h"),
    ("SME-02", "SME", "Repeated transfers just under the PIN-less limit",
     "near_limit_count", 4, ".02", "Repeated UPI transfers just under the PIN-less limit"),
    ("SME-03", "SME", "One invoice split across many beneficiaries",
     "split_count", 6, ".02", "One invoice split across many beneficiaries same day"),

    ("BEH-01", "BEH", "Dormant account suddenly transacting at high value",
     "dormancy_days", 180, ".03", "Dormant account transacting at high value"),
    ("BEH-02", "BEH", "Borrowal account liquidated with funds from another bank",
     "external_funding_pct", 0.6, ".02", "Borrowal account liquidated with funds from another bank"),
    ("BEH-03", "BEH", "Sale proceeds not routed through the lender",
     "unrouted_pct", 0.5, ".02", "Sale proceeds not routed through the lender"),

    ("LAY-01", "LAY", "Rapid pass-through: credit drained straight out",
     "outflow_ratio", 0.90, ".03", "Outflow >= 90% of recent inflow drained within window"),
    ("LAY-02", "LAY", "Fan-in/fan-out hub: many counterparties in a window",
     "distinct_counterparties", 40, ".03", "40+ distinct counterparties in window (fan-in/fan-out hub)"),
    ("LAY-03", "LAY", "Circular flow returning value to origin",
     "cycle_hops", 3, ".03", "Value returned to origin within 3 hops"),
    ("LAY-04", "LAY", "Accounts sharing a device or IP fingerprint",
     "cluster_size", 4, ".02", "4-9 accounts share a device fingerprint"),
    # AI/ML roadmap Phase 3 (BRD OD-06/s19; HLD AD-15/s16). GNN-scored, not a fixed
    # formula - a GraphSAGE embedding per account, scored for anomalousness the same
    # calibrated-percentile way Phase 2's VEL-04 is (detection/model_scoring.py).
    # Topology, not velocity, hence LAY rather than VEL. Refreshed periodically
    # (scripts/refresh_ring_scores.py), not scored inline per transaction like VEL-04 -
    # see features.py's NEEDS_MODEL and engine.py's separate LAY-05 lookup.
    ("LAY-05", "LAY", "GNN-scored fraud-ring membership, above threshold",
     "ring_score", 0.7, ".02", "Graph-embedding anomaly score above threshold "
     "(this account's position in the payment graph is atypical for the tenant)"),

    ("CPT-01", "CPT", "High-value transfer to a very new beneficiary",
     "beneficiary_age_hours", 24, ".02", "High-value transfer to a beneficiary added <24h ago"),
    ("CPT-02", "CPT", "Counterparty on a sanctions, negative, or fraud-registry list",
     "list_match_score", 0.85, ".03",
     "Counterparty present on a negative, sanctions, or Central Fraud Registry list"),
    ("CPT-03", "CPT", "Same collateral charged to multiple lenders",
     "lender_count", 2, ".02", "Same collateral charged to multiple lenders"),

    ("CHN-01", "CHN", "New device, new beneficiary and high value in one session",
     "risk_signals", 3, ".03", "New device + new beneficiary + high value in one session"),
    ("CHN-02", "CHN", "Geo-velocity implying impossible travel",
     "implied_kmph", 900, ".02", "Geo-velocity implies impossible travel"),
    ("CHN-03", "CHN", "Transaction outside the account's usual hours",
     "hour_deviation_sigma", 2.0, ".01", "Transaction outside the account's usual hours"),
    # AI/ML roadmap Phase 1 (device & behavioural biometrics - BRD OD-06/s19). The score
    # is sourced from an integrated device-fingerprinting / behavioural-biometric
    # provider (e.g. VideoPD, the Human Fraud Detection Framework) - this platform
    # consumes it as a feature, it does not compute it. See features.py's
    # NEEDS_EXTERNAL_DATA: unmeasurable until a tenant's feed actually supplies it.
    ("CHN-04", "CHN", "Elevated device-posture risk score at transaction time",
     "device_risk_score", 0.7, ".02", "Device-posture risk score above threshold "
     "(jailbreak/root, emulator, SIM-swap or similar signals)"),
    ("CHN-05", "CHN", "Elevated behavioural-biometric anomaly score at transaction time",
     "behavior_anomaly_score", 0.7, ".02", "Behavioural-biometric anomaly score above "
     "threshold (typing cadence, touch pressure, session/gesture pattern deviation)"),

    ("TBM-01", "TBM", "Merchanting trade with an undisclosed import leg",
     "undisclosed_legs", 1, ".02", "Merchanting trade - import leg not disclosed"),
    ("TBM-02", "TBM", "LC opened for local trade with a related party",
     "related_party_flag", 1, ".02", "LC opened for local trade with a related party"),
    ("TBM-03", "TBM", "Foreign bills outstanding beyond realisation norms",
     "days_outstanding", 270, ".02", "Foreign bills outstanding beyond realisation norms"),

    ("CBS-01", "CBS", "Loan funds routed to unrelated third parties",
     "diverted_pct", 0.4, ".03", "Loan funds routed to unrelated third parties"),
    ("CBS-02", "CBS", "Heavy cash withdrawal in a loan account",
     "cash_ratio", 0.3, ".02", "Heavy cash withdrawal in a loan account"),
    ("CBS-03", "CBS", "Large transactions with inter-connected group companies",
     "group_exposure_pct", 0.35, ".02", "Large transactions with inter-connected group companies"),
    # RBI's 2016 EWS annexure item 2 ("Bouncing of high value cheques") - previously
    # mapped to nothing in docs/rbi_ews_mapping.py, with the note "Needs cheque-return
    # data from the CBS". A count, not a ratio, so it has none of CBS-01/02/03's
    # undefined-denominator trap: zero returns is a real, measured "clean", not an
    # absent fact - see cbs_features.py's observe_loan.
    ("CBS-04", "CBS", "Repeated dishonour of cheques presented against the account",
     "cheque_return_count", 3, ".02", "3+ cheques returned in the observation window"),
    # A distinct facility-conduct signal from CBS-02's cash ratio: this is about
    # drawing beyond what was ever sanctioned, not about how the money was drawn.
    ("CBS-05", "CBS", "Drawings persistently exceeding the sanctioned overdraft "
     "or cash-credit limit", "od_breach_ratio", 1.0, ".03",
     "Peak utilisation exceeded the sanctioned overdraft/cash-credit limit"),
    # RBI 2016 EWS annexure item 6. A count, same shape as CBS-04 - the invocation or
    # devolvement is the fact, however many times it happens.
    ("CBS-06", "CBS", "Frequent invocation of bank guarantees and devolvement of "
     "letters of credit", "bg_lc_event_count", 2, ".02",
     "2+ BG invocations/LC devolvements in the observation window"),
    # RBI 2016 EWS annexure item 13. Even one facility explicitly sanctioned to fund
    # interest on an existing exposure is a serious red flag - it is how an account
    # avoids NPA classification without actually improving, so the threshold is 1, not
    # a "frequency" figure the way CBS-04/06 use.
    ("CBS-07", "CBS", "Funding of interest by sanctioning an additional facility",
     "interest_funding_count", 1, ".03",
     "A fresh facility was sanctioned specifically to fund interest on existing exposure"),
    # RBI 2016 EWS annexure item 5. A count of instalments paid materially late against
    # their own due_date, same counting shape as CBS-04/06 - see cbs_features.py's
    # DELAY_GRACE_DAYS for what "materially" means here.
    ("CBS-08", "CBS", "Delay in payment of outstanding dues",
     "delayed_repayment_count", 2, ".02",
     "2+ instalments paid more than a week past their due date in the observation window"),

    ("QUAL-01", "QUAL", "Frequent change of primary banker",
     "banker_changes_24m", 2, ".01", "Frequent change of primary banker"),
    ("QUAL-02", "QUAL", "Frequent ad-hoc / general-purpose loan requests",
     "adhoc_requests", 3, ".01", "Frequent ad-hoc / general-purpose loan requests"),
    ("QUAL-03", "QUAL", "Material facts concealed, surfaced post-disbursement",
     "concealment_findings", 1, ".02", "Material facts concealed, surfaced post-disbursement"),

    # LOS: loan-origination fraud (BR-214). Evaluated at application time, not folded
    # into payment-transaction scoring - see detection/los_features.py.
    ("LOS-01", "LOS", "Declared income inconsistent with the applicant's own account activity",
     "declared_income_ratio", 3.0, ".01",
     "Declared income far exceeds what the applicant's own account activity supports"),
    ("LOS-02", "LOS", "Collateral valuation running high against comparable valuations",
     "valuation_peer_ratio", 1.4, ".01",
     "Valuation running high against other valuers' assessments of comparable assets"),
    ("LOS-03", "LOS", "Same applicant identity behind multiple loan applications",
     "linked_application_count", 2, ".01",
     "Same applicant identity found on multiple distinct loan applications"),
]

# Qualitative indicators are fed from CBS events and relationship-manager input rather
# than the transaction stream. The RBI framework requires both kinds, and a qualitative
# rule that never fires on payment traffic is expected, not a defect.
QUALITATIVE_FAMILIES = {"QUAL"}


#: Rules whose observation fires when it is *below* the threshold. "Beneficiary added in
#: the last 24 hours" cannot be expressed as observed >= 24: a two-hour-old payee is more
#: suspicious than a twenty-hour-old one, and a plain >= comparison would fire on neither.
#: Every other rule in the catalogue is "at or above", which stays the default.
#:
#: LAY-03 belongs here for the same reason, and its absence was a live defect. Its own
#: stated reason is "Value returned to origin *within* 3 hops" - within is at most - but
#: it was picking up the gte default. Measured against real traffic that fired on 82% of
#: accounts, because almost every account eventually sits on some long cycle; a tight
#: 2-3 hop round trip is the layering shape, and a five-hop one is ordinary commerce.
#: Under lte it fires on ~16%, which is a band the bank can then tune. The observation
#: itself is comparator-agnostic: features.observe emits nothing at all when there is no
#: cycle, so neither direction can fire on an account that has no circular flow.
BELOW_THRESHOLD_RULES = {"VEL-02", "CPT-01", "LAY-03"}


def comparator_for(rule_id: str) -> str:
    return "lte" if rule_id in BELOW_THRESHOLD_RULES else "gte"


def _rule_body(rule_id: str, family: str, desc: str, unit: str,
               threshold: float, ref: str, reason: str) -> dict:
    """One rule in Tazama's band shape. The top band carries the operative threshold;
    lower bands exist so a score gradient is possible rather than a bare on/off."""
    return {
        "id": rule_id,
        "family": family,
        "desc": desc,
        "qualitative": family in QUALITATIVE_FAMILIES,
        "comparator": comparator_for(rule_id),
        "config": {
            "observedUnit": unit,
            "parameters": [
                {"ParameterName": "windowMs", "ParameterValue": 86_400_000,
                 "ParameterType": "number"},
            ],
            "exitConditions": [
                {"subRuleRef": ".x00", "reason": "Not assessable for this transaction"},
            ],
            "bands": [
                {"subRuleRef": ".01", "upperLimit": threshold * 0.6,
                 "reason": f"{reason} (below threshold)"},
                {"subRuleRef": ".02", "lowerLimit": threshold * 0.6,
                 "upperLimit": threshold, "reason": f"{reason} (approaching threshold)"},
                {"subRuleRef": ref, "lowerLimit": threshold, "reason": reason,
                 "operative": True},
            ],
        },
    }


#: Rules that only make sense against a lending relationship - a borrowal/loan account,
#: pledged collateral, a disbursement, a "primary banker". Checked against the actual
#: EWS chapter text of all nine 2026 Directions (RBI/DoS/2026-27/412..463): every entity
#: type except Payments Banks carries the borrower/financial-performance EWS language;
#: the PB Direction carries none of it, because a PB licence cannot extend credit at all
#: (RBI (PBs - Fraud Risk Management) Directions, 2026, Chapter III has no credit-related
#: EWS section, unlike every other Direction's Chapter III(B)). See policy.py's
#: ENTITY_TYPES for the per-entity-type "can_extend_credit" flag this is gated on.
#:
#: TBM (trade-based: merchanting trade, LC, foreign bills) is deliberately NOT in this
#: set despite looking similarly gateable. Trade-finance authorisation is a separate AD
#: (Authorised Dealer) licence layered on top of an entity type, not a fixed property of
#: the entity type itself - none of the nine Directions' own text ties it to entity type
#: either (checked: none of them mention Letters of Credit, merchanting trade or foreign
#: bills at all - that vocabulary comes from RBI's older 2016 EWS circular that
#: docs/rbi_ews_mapping.py maps against, not from the 2026 Directions). Gating TBM by
#: entity_type would be guessing at a fact these documents don't state; it needs a
#: per-tenant AD-licence signal instead, which does not exist yet - left ungated on
#: purpose rather than encoding a plausible-looking wrong default.
CREDIT_LINKED_RULES = {
    "BEH-02", "BEH-03",            # borrowal account conduct
    "CPT-03",                       # collateral charged to multiple lenders
    "CBS-01", "CBS-02", "CBS-03",   # loan-account misuse
    "CBS-04",                       # cheque returns - borrower-conduct EWS signal, RBI item 2
    "CBS-05",                       # overdraft/cash-credit breach - a credit facility by definition
    "CBS-06",                       # BG invocation / LC devolvement - trade-finance credit facilities
    "CBS-07",                       # interest funded by a fresh sanction - a credit facility by definition
    "CBS-08",                       # delayed instalments - a repayment schedule is a credit facility fact
    "QUAL-01", "QUAL-02", "QUAL-03",  # banker changes, loan requests, post-disbursement concealment
    # LOS (BR-214): a loan application or a collateral valuation cannot exist without a
    # credit facility to originate. Same gate as the rest of this set, for the same
    # reason - an entity that cannot extend credit at all should never see these seeded
    # as if it might.
    "LOS-01", "LOS-02", "LOS-03",
}


def rule_configs(can_extend_credit: bool = True) -> list[dict]:
    """Every rule as a seedable config record.

    ``can_extend_credit=False`` drops the credit-linked rules (see
    ``CREDIT_LINKED_RULES``) - seeded, not just hidden, so a tenant whose licence cannot
    extend credit never has those rules active in the first place. Defaults to the full
    catalogue so existing callers are unaffected.
    """
    return [
        {"kind": "rule", "name": rid, "version": "1.0.0",
         "body": _rule_body(rid, fam, desc, unit, thr, ref, reason)}
        for rid, fam, desc, unit, thr, ref, reason in _RULES
        if can_extend_credit or rid not in CREDIT_LINKED_RULES
    ]


def operative_thresholds() -> dict[str, dict]:
    """rule_id -> {family, unit, threshold, ref, reason} for the operative band.

    Detection reads this, so a threshold retuned in the console changes what fires.
    """
    return {
        rid: {"family": fam, "unit": unit, "threshold": float(thr),
              "sub_rule_ref": ref, "reason": reason,
              "comparator": comparator_for(rid),
              "qualitative": fam in QUALITATIVE_FAMILIES}
        for rid, fam, _desc, unit, thr, ref, reason in _RULES
    }


ALL_RULE_IDS = [r[0] for r in _RULES]
