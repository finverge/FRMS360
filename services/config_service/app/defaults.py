"""Default Tazama configs laid down for every new tenant during onboarding.

The LAY (mule / layering) typology and its rules ship as the starter detection pack so
a freshly-onboarded bank has a working baseline it can then tune. Thresholds are
illustrative and meant to be calibrated per tenant.
"""

MULE_LAYERING_TYPOLOGY = {
    "id": "mule-layering@1.0.0",
    "cfg": "1.0.0",
    "desc": "Mule-account layering typology (LAY family)",
    "rules": [
        {"id": "018@1.0.0", "cfg": "1.0.0", "termId": "t018",
         "wghts": [{"ref": ".x00", "wght": 0}, {"ref": ".01", "wght": 0},
                   {"ref": ".02", "wght": 50}, {"ref": ".03", "wght": 150}]},
        {"id": "024@1.0.0", "cfg": "1.0.0", "termId": "t024",
         "wghts": [{"ref": ".x00", "wght": 0}, {"ref": ".01", "wght": 30},
                   {"ref": ".02", "wght": 70}, {"ref": ".03", "wght": 120}]},
        {"id": "030@1.0.0", "cfg": "1.0.0", "termId": "t030",
         "wghts": [{"ref": ".x00", "wght": 0}, {"ref": ".01", "wght": 40},
                   {"ref": ".02", "wght": 90}, {"ref": ".03", "wght": 150}]},
        {"id": "044@1.0.0", "cfg": "1.0.0", "termId": "t044",
         "wghts": [{"ref": ".x00", "wght": 0}, {"ref": ".01", "wght": 30},
                   {"ref": ".02", "wght": 80}, {"ref": ".03", "wght": 140}]},
    ],
    "expression": "t018 + t024 + t030 + t044",
    "workflow": {"alertThreshold": 150, "interdictionThreshold": 300},
}

from .ews_catalogue import rule_configs  # noqa: E402
from .policy import ENTITY_TYPES, policy_body  # noqa: E402


def default_configs_for(entity_type: str = "commercial_bank",
                        ucb_tier: int | None = None) -> list[dict]:
    """Seed set for a new tenant, including the FRM policy for its entity type.

    A UCB and a commercial bank are governed by different Master Directions, so their
    clocks and escalation floors must not start out identical. Likewise, the rule
    catalogue itself is no longer identical for every tenant: a Payments Bank licence
    cannot extend credit, so the credit-linked rules (CBS/QUAL loan-conduct, borrowal-
    account BEH/CPT) are never seeded for one in the first place - see
    ews_catalogue.CREDIT_LINKED_RULES for exactly which rules and why. Everything else
    (velocity, structuring, layering/mule, counterparty, channel) applies to every
    entity type and is seeded unconditionally.
    """
    can_extend_credit = ENTITY_TYPES.get(entity_type, ENTITY_TYPES["commercial_bank"])[
        "can_extend_credit"]
    return [
        {"kind": "typology", "name": "mule-layering", "version": "1.0.0",
         "body": MULE_LAYERING_TYPOLOGY},
        *rule_configs(can_extend_credit),
        {"kind": "policy", "name": "frm-policy", "version": "1.0.0",
         "body": policy_body(entity_type, ucb_tier)},
    ]
