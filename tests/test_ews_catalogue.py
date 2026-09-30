"""The EWS rule catalogue is no longer identical for every tenant.

A Payments Bank licence cannot extend credit (see policy.py's ENTITY_TYPES docstring -
checked against all nine 2026 Directions' own Chapter III text, not assumed). Rules that
only make sense against a lending relationship must therefore never be seeded for one,
not just hidden in the console after the fact. These tests exist to stop that regressing
back to a single, uniform catalogue for every entity type.
"""
from services.config_service.app.ews_catalogue import (
    CREDIT_LINKED_RULES, _RULES, rule_configs,
)

_ALL_RULE_IDS = {rid for rid, *_ in _RULES}


def test_credit_linked_rules_are_real_rule_ids():
    """A typo in CREDIT_LINKED_RULES would silently fail to exclude anything - it's a
    set literal, not a foreign key, so nothing else would catch that."""
    unknown = CREDIT_LINKED_RULES - _ALL_RULE_IDS
    assert not unknown, f"CREDIT_LINKED_RULES references non-existent rule ids: {unknown}"


def test_default_still_returns_the_full_catalogue():
    """Existing callers that don't pass can_extend_credit must be unaffected."""
    assert {c["name"] for c in rule_configs()} == _ALL_RULE_IDS


def test_can_extend_credit_true_returns_the_full_catalogue():
    assert {c["name"] for c in rule_configs(can_extend_credit=True)} == _ALL_RULE_IDS


def test_can_extend_credit_false_drops_exactly_the_credit_linked_rules():
    kept = {c["name"] for c in rule_configs(can_extend_credit=False)}
    assert kept == _ALL_RULE_IDS - CREDIT_LINKED_RULES
    assert kept.isdisjoint(CREDIT_LINKED_RULES)


def test_credit_linked_rules_cover_the_loan_and_borrowal_families():
    """Sanity check on the set's contents, not just its mechanics - these are the
    families whose rule text explicitly names a borrower, a loan or a lender."""
    assert {"CBS-01", "CBS-02", "CBS-03", "CBS-04", "CBS-05"} <= CREDIT_LINKED_RULES  # loan-account misuse
    assert {"BEH-02", "BEH-03"} <= CREDIT_LINKED_RULES            # borrowal-account conduct
    assert "CPT-03" in CREDIT_LINKED_RULES                        # collateral, multiple lenders
    assert {"QUAL-01", "QUAL-02", "QUAL-03"} <= CREDIT_LINKED_RULES  # banker/loan-request conduct
    # BR-214: an application or a valuation cannot exist without a credit facility to
    # originate - same gate, same reason.
    assert {"LOS-01", "LOS-02", "LOS-03"} <= CREDIT_LINKED_RULES


def test_universal_families_are_never_gated():
    """VEL/SME/LAY/CPT(-01/-02)/CHN apply to any transacting entity, credit or not - a
    Payments Bank still has velocity, structuring, mule and channel risk."""
    universal_prefixes = ("VEL-", "SME-", "LAY-", "CHN-")
    for rid in _ALL_RULE_IDS:
        if rid.startswith(universal_prefixes):
            assert rid not in CREDIT_LINKED_RULES, f"{rid} should not be credit-gated"
    assert "CPT-01" not in CREDIT_LINKED_RULES
    assert "CPT-02" not in CREDIT_LINKED_RULES
    assert "BEH-01" not in CREDIT_LINKED_RULES  # dormant-account monitoring, not credit-specific


def test_trade_based_rules_are_deliberately_not_gated_by_entity_type():
    """TBM (merchanting trade / LC / foreign bills) looks like it should be gated the
    same way, but AD (Authorised Dealer) forex authorisation is a separate licence layered
    on top of an entity type, not a fixed property of it, and none of the nine 2026
    Directions tie it to entity type either. See ews_catalogue.py's comment on
    CREDIT_LINKED_RULES for the full reasoning - this test just guards against someone
    "fixing" that by guessing."""
    assert not ({"TBM-01", "TBM-02", "TBM-03"} & CREDIT_LINKED_RULES)
