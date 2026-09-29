"""Per-tenant Fraud Risk Management policy.

RBI issued the July 2024 Master Directions as **separate** instruments for commercial
banks/AIFIs, for co-operative banks (UCBs / StCBs / CCBs) and for NBFCs/HFCs. They share
a spine - board-approved FRM policy, EWS, red-flagging, natural justice, timely reporting -
but differ in who governs, who is reported to, and at what size a case escalates.

Hard-coding a 24-hour SLA or a 21-day response window therefore cannot be right for both
HDFC and a Tier-1 urban co-operative bank. Everything policy-driven lives here, per tenant,
so detection, the compliance clocks and the dashboards all read the same board-approved
numbers.

**The numeric defaults below are placeholders.** They are shaped correctly and differ
sensibly by entity type, but every one of them must be set from the tenant's own
board-approved FRM policy and validated against the live Master Directions before use.
The platform's job is to make them explicit, versioned and auditable - not to assert what
a given bank's policy says.
"""

# ---- entity types, mapped to the direction that governs them ----
# RBI withdrew the three combined 15-July-2024 Directions on 31 July 2026 and replaced
# them with nine entity-specific ones (RBI/DoS/2026-27/412 through /463). Two of the
# three 2024 groupings split into more than one 2026 successor - Commercial Banks and
# AIFIs are no longer one document, and neither are Urban and Rural Co-operative Banks -
# so this table now has more rows than families of tenant, not the same count with new
# dates. See docs (D:\Finverge\Docs\Products\FRMS\RBI) for the source PDFs and the
# comparison this was built from.
#
# "can_extend_credit" is checked against each Direction's own Chapter III text, not
# assumed: every entity type below carries the borrower/financial-performance EWS
# language ("...could be based on transactional data of accounts, financial performance
# of borrowers, market intelligence and conduct of borrowers") except Payments Banks,
# whose Direction has no credit-related EWS section at all - a PB licence cannot extend
# credit. ews_catalogue.py's rule_configs() reads this flag to decide whether the
# credit-linked rule families are even seeded for a tenant of this entity type.
ENTITY_TYPES = {
    "commercial_bank": {
        "label": "Commercial Bank",
        "direction": "Fraud Risk Management in Commercial Banks, 2026 (RBI/DoS/2026-27/412)",
        "regulators": ["RBI"],
        "can_extend_credit": True,
    },
    "aifi": {
        "label": "All India Financial Institution (AIFI)",
        "direction": "Fraud Risk Management in All India Financial Institutions, 2026 (RBI/DoS/2026-27/457)",
        # EXIM Bank, NABARD, NHB, SIDBI, NaBFID - named individually in the Direction,
        # not by class. Same CRILC linkage as Commercial Banks.
        "regulators": ["RBI"],
        "can_extend_credit": True,
    },
    "urban_cooperative": {
        "label": "Urban Co-operative Bank (UCB)",
        "direction": "Fraud Risk Management in Urban Co-operative Banks, 2026 (RBI/DoS/2026-27/439)",
        "regulators": ["RBI"],
        "can_extend_credit": True,
    },
    "state_cooperative": {
        "label": "State Co-operative Bank (StCB)",
        "direction": "Fraud Risk Management in Rural Co-operative Banks, 2026 (RBI/DoS/2026-27/451)",
        # Rural co-operative structure is supervised through NABARD. The 2026 Direction
        # reports to NABARD by name (Chapter VI), not RBI - see rules.py's policy().
        "regulators": ["RBI", "NABARD"],
        "can_extend_credit": True,
    },
    "central_cooperative": {
        "label": "Central / District Co-operative Bank (CCB)",
        "direction": "Fraud Risk Management in Rural Co-operative Banks, 2026 (RBI/DoS/2026-27/451)",
        "regulators": ["RBI", "NABARD"],
        "can_extend_credit": True,
    },
    "rrb": {
        "label": "Regional Rural Bank",
        "direction": "Fraud Risk Management in Regional Rural Banks, 2026 (RBI/DoS/2026-27/454)",
        "regulators": ["RBI", "NABARD"],
        "can_extend_credit": True,
    },
    "local_area_bank": {
        "label": "Local Area Bank (LAB)",
        "direction": "Fraud Risk Management in Local Area Banks, 2026 (RBI/DoS/2026-27/446)",
        "regulators": ["RBI"],
        "can_extend_credit": True,
    },
    "small_finance_bank": {
        "label": "Small Finance Bank (SFB)",
        "direction": "Fraud Risk Management in Small Finance Banks, 2026 (RBI/DoS/2026-27/421)",
        "regulators": ["RBI"],
        "can_extend_credit": True,
    },
    "payments_bank": {
        "label": "Payments Bank (PB)",
        "direction": "Fraud Risk Management in Payments Banks, 2026 (RBI/DoS/2026-27/430)",
        "regulators": ["RBI"],
        # A PB licence does not permit extending credit facilities. Its Direction's
        # Chapter III has one EWS section ("...for Banking Transactions"), not the
        # governance-plus-credit-plus-non-credit split every lending entity type's
        # Direction has - confirmed by absence, not inference: none of the borrower/
        # financial-performance EWS language appears anywhere in the document.
        "can_extend_credit": False,
    },
    "nbfc": {
        "label": "NBFC",
        "direction": "Fraud Risk Management in NBFCs (incl. HFCs), 2026 (RBI/DoS/2026-27/463)",
        "regulators": ["RBI"],
        "can_extend_credit": True,
    },
    "hfc": {
        "label": "Housing Finance Company",
        "direction": "Fraud Risk Management in NBFCs (incl. HFCs), 2026 (RBI/DoS/2026-27/463)",
        # The 2026 Direction exempts HFCs from Chapters VI and VIII - fraud and
        # theft/burglary/dacoity/robbery reporting goes to NHB, not RBI. The filing
        # pipeline (BR-504/505) does not yet branch on this; see the gap-analysis note.
        "regulators": ["RBI", "NHB"],
        "can_extend_credit": True,
    },
}

# UCBs sit in RBI's four-tier regulatory framework; supervisory expectation scales with
# tier, so a Tier-1 bank should not be held to a Tier-4 operating tempo.
UCB_TIERS = {
    1: "Tier 1 - smallest deposit base, simplified expectations",
    2: "Tier 2",
    3: "Tier 3",
    4: "Tier 4 - largest, expectations approach commercial banks",
}

_L = 100_000 * 100        # one lakh, in paise
_CR = 10_000_000 * 100    # one crore, in paise


def _base() -> dict:
    """The spine every entity type shares."""
    return {
        # --- natural justice (SBI v. Rajesh Agarwal) ---
        "natural_justice_days": 21,
        "show_cause_within_days": 7,
        # --- regulatory clocks ---
        "fmr_filing_days": 7,
        "str_filing_days": 7,
        # Staff accountability runs on a far longer clock than the returns, because it is
        # an investigation into the bank's own people rather than a report. It must never
        # be allowed to hold up the filing - see accountability_model for why.
        "staff_accountability_days": 180,
        # The Directions prescribe a turnaround of not more than 30 days for examining
        # an EWS alert or trigger. This is the clock from alert raised to alert first
        # touched - distinct from stalled_case_days, which measures a case that is
        # already open, and from sla_breach_hours, which is an operational target the
        # bank sets for itself rather than a regulatory ceiling.
        "ews_examination_days": 30,
        # --- escalation thresholds, in paise ---
        "lea_referral_paise": 1 * _CR,
        "board_reporting_paise": 1 * _CR,
        "material_fraud_paise": 10 * _L,
        # --- detection scoring ---
        "severity_critical_score": 380,
        "severity_high_score": 260,
        "severity_medium_score": 150,
        # --- operations ---
        "sla_breach_hours": 24,
        "stalled_case_days": 30,
        "sla_hours_by_severity": {"critical": 4, "high": 12, "medium": 24, "low": 72},
        # --- retention (BR-713). Floors live in cp_common.retention and cannot be
        # configured below; these are the tenant-facing defaults. ---
        "retain_sessions_days": 90,
        "retain_notifications_days": 180,
        "retain_raw_ingest_days": 400,
        "retain_transactions_days": 365 * 5,
        "retain_alerts_days": 365 * 2,
        "retain_closed_cases_days": 365 * 5,
        "retain_fraud_records_days": 365 * 10,
        "retain_audit_days": 365 * 8,
        # --- governance bodies ---
        "special_committee": True,     # SCBMF - monitoring & follow-up of fraud cases
        "audit_committee": True,       # ACB
        "board_of_management": False,  # used by smaller UCBs in place of a full board cttee
        "board_review_frequency": "quarterly",
    }


def _for(entity_type: str, ucb_tier: int | None = None) -> dict:
    p = _base()

    if entity_type in ("urban_cooperative", "state_cooperative", "central_cooperative"):
        # Smaller institutions: longer operating clocks, lower escalation floors, and for
        # the smaller tiers a Board of Management rather than a full special committee.
        tier = ucb_tier or 1
        p.update({
            "sla_breach_hours": 48 if tier <= 2 else 24,
            "stalled_case_days": 45 if tier <= 2 else 30,
            "sla_hours_by_severity": ({"critical": 8, "high": 24, "medium": 48, "low": 120}
                                      if tier <= 2
                                      else {"critical": 6, "high": 18, "medium": 36, "low": 96}),
            "lea_referral_paise": 10 * _L if tier <= 2 else 50 * _L,
            "board_reporting_paise": 5 * _L if tier <= 2 else 25 * _L,
            "material_fraud_paise": 1 * _L,
            "special_committee": tier >= 3,
            "board_of_management": tier <= 2,
            "board_review_frequency": "quarterly",
        })
    elif entity_type in ("nbfc", "hfc"):
        p.update({
            "lea_referral_paise": 50 * _L,
            "board_reporting_paise": 25 * _L,
            "special_committee": True,
        })
    elif entity_type == "rrb":
        p.update({
            "sla_breach_hours": 36,
            "lea_referral_paise": 25 * _L,
            "board_reporting_paise": 10 * _L,
        })
    elif entity_type == "small_finance_bank":
        # A full bank with CRILC linkage and its own Cheque-Related-Frauds chapter
        # (2026 Direction), but newer and smaller in scale than a commercial bank -
        # RRB's tempo is the closest comparable size-class, not a claim that an SFB
        # is an RRB.
        p.update({
            "sla_breach_hours": 36,
            "lea_referral_paise": 25 * _L,
            "board_reporting_paise": 10 * _L,
        })
    elif entity_type == "local_area_bank":
        # Smallest, most geographically restricted bank category RBI licenses.
        # Gentler tempo, matching the pattern already used for the smaller UCB tiers.
        p.update({
            "sla_breach_hours": 48,
            "stalled_case_days": 45,
            "lea_referral_paise": 10 * _L,
            "board_reporting_paise": 5 * _L,
            "material_fraud_paise": 1 * _L,
        })
    elif entity_type == "payments_bank":
        # Cannot extend credit facilities - a Payments Bank licence does not permit
        # lending. The credit-linked thresholds above (lea_referral_paise and friends)
        # are therefore not expected to be exercised; what actually governs a PB is
        # Chapter III(C) of its 2026 Direction, the non-credit-transaction EWS
        # framework, which this platform does not yet model as a distinct threshold
        # set. Left at base defaults deliberately rather than invented numbers - see
        # the gap-analysis recommendation to confirm with a PB tenant's own board
        # before activating a live policy.
        pass
    return p


def policy_body(entity_type: str = "commercial_bank", ucb_tier: int | None = None) -> dict:
    """The seedable FRM policy config for a tenant."""
    entity_type = entity_type if entity_type in ENTITY_TYPES else "commercial_bank"
    meta = ENTITY_TYPES[entity_type]
    return {
        "entity_type": entity_type,
        "entity_label": meta["label"],
        "governing_direction": meta["direction"],
        "reporting_to": meta["regulators"] + ["FIU-IND"],   # PMLA applies to all
        "ucb_tier": ucb_tier if entity_type == "urban_cooperative" else None,
        # Rule 7, PMLA Rules 2005: a board/senior-management designation, not a platform
        # default - there is no sensible seed value, so this stays absent until the
        # tenant's own policy version names one. It rides the same attestation gate as
        # the rest of this body (see attestation.py): naming a Principal Officer without
        # board authority behind it is exactly the kind of unattributable fact BR-104
        # exists to prevent.
        "principal_officer_name": None,
        "thresholds": _for(entity_type, ucb_tier),
        "_note": (
            "Placeholder values. Every number must be set from this entity's "
            "board-approved FRM policy and validated against the live RBI Master "
            "Direction named above before production use."
        ),
    }


# Knobs that detection, the metric registry and the clocks read at query time.
NUMERIC_KEYS = [
    "natural_justice_days", "show_cause_within_days", "fmr_filing_days", "str_filing_days",
    "staff_accountability_days",
    "lea_referral_paise", "board_reporting_paise", "material_fraud_paise",
    "severity_critical_score", "severity_high_score", "severity_medium_score",
    "sla_breach_hours", "stalled_case_days",
    "retain_sessions_days", "retain_notifications_days", "retain_raw_ingest_days",
    "retain_transactions_days", "retain_alerts_days", "retain_closed_cases_days",
    "retain_fraud_records_days", "retain_audit_days",
]


def policy_defaults() -> dict:
    """Fallback used when a tenant has no policy config yet."""
    return policy_body("commercial_bank")["thresholds"]
