"""Per-tenant FRM policy.

RBI issued separate Master Directions for commercial banks, co-operative banks and
NBFCs. A hard-coded SLA or escalation floor therefore cannot be correct for every
tenant, and these tests exist to stop one creeping back in.
"""
import pytest

from services.config_service.app.policy import ENTITY_TYPES, policy_body


def test_every_entity_type_names_its_governing_direction():
    for key, meta in ENTITY_TYPES.items():
        body = policy_body(key)
        assert body["governing_direction"], f"{key} has no governing direction"
        assert body["entity_label"]
        assert body["thresholds"]["natural_justice_days"] > 0


# -------------------------------------------------- 2026 restructuring (RBI/DoS/2026-27)
# RBI withdrew the three combined 15-July-2024 Directions on 31 July 2026 and replaced
# them with nine entity-specific ones. These guard against the exact regression that
# prompted this file's own docstring: a hard-coded citation or threshold creeping back
# in, this time a stale 2024 reference instead of a stale number.
def test_no_governing_direction_still_cites_2024():
    """The 2024 Directions are withdrawn - RBI's own archive stamps the PDF on every
    page. Nothing here may still point at one."""
    for key, meta in ENTITY_TYPES.items():
        assert "2024" not in meta["direction"], f"{key} still cites a 2024 Direction"
        assert "2026" in meta["direction"], f"{key} does not cite a 2026 Direction"


def test_commercial_bank_and_aifi_are_no_longer_one_direction():
    """The 2024 direction covered both; the 2026 split them into RBI/DoS/2026-27/412
    and /457 respectively. Citing the same string for both would be the old, wrong
    behaviour re-appearing."""
    assert ENTITY_TYPES["commercial_bank"]["direction"] != ENTITY_TYPES["aifi"]["direction"]
    assert "412" in ENTITY_TYPES["commercial_bank"]["direction"]
    assert "457" in ENTITY_TYPES["aifi"]["direction"]


def test_urban_and_rural_cooperatives_are_no_longer_one_direction():
    """Same split, different pair: UCB (439) separated from StCB/CCB, now renamed
    'Rural Co-operative Banks' (451)."""
    assert (ENTITY_TYPES["urban_cooperative"]["direction"]
            != ENTITY_TYPES["state_cooperative"]["direction"])
    assert "439" in ENTITY_TYPES["urban_cooperative"]["direction"]
    assert "451" in ENTITY_TYPES["state_cooperative"]["direction"]
    assert "451" in ENTITY_TYPES["central_cooperative"]["direction"]


def test_rrb_is_no_longer_bundled_with_commercial_bank():
    assert ENTITY_TYPES["rrb"]["direction"] != ENTITY_TYPES["commercial_bank"]["direction"]
    assert "454" in ENTITY_TYPES["rrb"]["direction"]


@pytest.mark.parametrize("key", ["aifi", "local_area_bank", "small_finance_bank",
                                 "payments_bank"])
def test_the_four_newly_added_entity_types_produce_a_complete_policy(key):
    """These four had no entity_type at all before this change - the RBI Direction
    existed, Fraud360 had no way to represent a tenant under it."""
    body = policy_body(key)
    assert body["governing_direction"]
    assert body["entity_label"]
    assert body["thresholds"]["natural_justice_days"] > 0
    assert body["principal_officer_name"] is None  # no sensible default; see policy.py


def test_payments_bank_is_left_at_base_defaults_deliberately():
    """A Payments Bank cannot extend credit facilities - the credit-linked thresholds
    a lending institution needs are not invented numbers for it. Confirms the
    intentional no-op in _for(), not an accidental omission."""
    from services.config_service.app.policy import _base
    pb = policy_body("payments_bank")["thresholds"]
    base = _base()
    for key in ("lea_referral_paise", "board_reporting_paise", "material_fraud_paise",
               "sla_breach_hours"):
        assert pb[key] == base[key], f"payments_bank.{key} diverged from base unexpectedly"


def test_small_finance_bank_and_local_area_bank_get_their_own_tempo():
    """Distinct from each other and from the base default - not just aliases for an
    existing type."""
    base = policy_body("commercial_bank")["thresholds"]
    sfb = policy_body("small_finance_bank")["thresholds"]
    lab = policy_body("local_area_bank")["thresholds"]
    assert sfb["sla_breach_hours"] != base["sla_breach_hours"]
    assert lab["sla_breach_hours"] != base["sla_breach_hours"]
    assert lab["lea_referral_paise"] < sfb["lea_referral_paise"], \
        "a Local Area Bank should not have a higher escalation floor than an SFB"


def test_the_new_entity_types_are_accepted_by_tenant_onboarding():
    """tenant_service duplicates this enum in its own schema (no cross-service import -
    see schemas.py's TenantCreate). This is the regression that duplication risks: the
    two lists drifting apart silently."""
    from services.tenant_service.app.schemas import TenantCreate
    for key in ("aifi", "local_area_bank", "small_finance_bank", "payments_bank"):
        t = TenantCreate(slug="test-tenant", legal_name="X", display_name="X",
                         entity_type=key, admin_email="a@b.com",
                         admin_password="longenoughpw")
        assert t.entity_type == key


def test_rural_cooperatives_report_to_nabard():
    """StCBs/CCBs are supervised through NABARD; commercial banks are not."""
    for key in ("state_cooperative", "central_cooperative", "rrb"):
        assert "NABARD" in policy_body(key)["reporting_to"], f"{key} must report to NABARD"
    assert "NABARD" not in policy_body("commercial_bank")["reporting_to"]
    assert "NHB" in policy_body("hfc")["reporting_to"]


def test_pmla_applies_to_every_entity_type():
    for key in ENTITY_TYPES:
        assert "FIU-IND" in policy_body(key)["reporting_to"]


# --------------------------------------------------- can_extend_credit / rule seeding
# A PB licence cannot extend credit (see ENTITY_TYPES's own comment) - this must be the
# one and only entity type carrying can_extend_credit=False, or the rule catalogue seeded
# at onboarding (defaults.py:default_configs_for) silently drifts from what each entity
# type is actually licensed to do. See test_ews_catalogue.py for the rule-level tests.
def test_every_entity_type_names_whether_it_can_extend_credit():
    for key, meta in ENTITY_TYPES.items():
        assert isinstance(meta.get("can_extend_credit"), bool), \
            f"{key} has no can_extend_credit flag"


def test_only_payments_bank_cannot_extend_credit():
    non_lenders = [k for k, m in ENTITY_TYPES.items() if not m["can_extend_credit"]]
    assert non_lenders == ["payments_bank"]


def test_default_configs_for_payments_bank_excludes_credit_linked_rules():
    from services.config_service.app.defaults import default_configs_for
    from services.config_service.app.ews_catalogue import CREDIT_LINKED_RULES

    seeded = default_configs_for("payments_bank")
    rule_names = {c["name"] for c in seeded if c["kind"] == "rule"}
    assert rule_names.isdisjoint(CREDIT_LINKED_RULES)
    # Still gets everything else - this is exclusion of specific rules, not a stripped
    # starter pack.
    assert any(c["kind"] == "typology" for c in seeded)
    assert any(c["kind"] == "policy" for c in seeded)


def test_default_configs_for_a_lending_entity_type_keeps_the_full_catalogue():
    from services.config_service.app.defaults import default_configs_for
    from services.config_service.app.ews_catalogue import _RULES

    seeded = default_configs_for("commercial_bank")
    rule_names = {c["name"] for c in seeded if c["kind"] == "rule"}
    assert rule_names == {rid for rid, *_ in _RULES}


def test_small_cooperatives_get_a_gentler_operating_tempo():
    """A Tier-1 UCB should not be held to a large commercial bank's clock."""
    big = policy_body("commercial_bank")["thresholds"]
    small = policy_body("urban_cooperative", 1)["thresholds"]
    assert small["sla_breach_hours"] > big["sla_breach_hours"]
    assert small["stalled_case_days"] > big["stalled_case_days"]
    assert small["lea_referral_paise"] < big["lea_referral_paise"]


def test_ucb_tier_changes_governance_body():
    """Smaller UCBs operate through a Board of Management rather than a full SCBMF."""
    t1 = policy_body("urban_cooperative", 1)["thresholds"]
    t4 = policy_body("urban_cooperative", 4)["thresholds"]
    assert t1["board_of_management"] and not t1["special_committee"]
    assert t4["special_committee"] and not t4["board_of_management"]


def test_policy_reaches_analytics(tid):
    from services.analytics_service.app import rules as bridge
    bridge.invalidate()
    pol = bridge.policy(tid)
    assert pol["available"], "analytics could not read the tenant's policy"
    assert pol["entity_type"] == "urban_cooperative"
    assert pol["values"]["sla_breach_hours"] == 48, "policy value did not reach analytics"


def test_metrics_use_the_policy_not_a_constant(analytics_client, token_for, tid, monkeypatch):
    """Changing the SLA window must change the SLA-breach count."""
    from services.analytics_service.app import rules as bridge
    h = token_for("risk_manager")

    def _with(hours):
        bridge.invalidate()
        vals = {**bridge._FALLBACK_POLICY, "sla_breach_hours": hours}
        monkeypatch.setattr(bridge, "policy_values", lambda t: vals)
        return analytics_client.get(
            f"/analytics/{tid}/risk_manager", headers=h
        ).json()["metrics"]["sla_breach_count"]["value"]

    wide, narrow = _with(100_000), _with(1)
    assert narrow > wide, "sla_breach_count ignored the policy window"


# ------------------------------------------------------- console policy editing
# Previously ConfigCreate.kind accepted only rule|typology|network_map - "policy" was
# refused at the schema before a tenant admin's request could reach the repository at
# all. These exercise the real HTTP path now that it is unblocked, not just the
# repository (see test_str_generation_names_the_principal_officer_from_policy in
# test_filings.py for the repository-level path).
#
# A "frm-policy-api-test" version is a *different name* from the seeded "frm-policy",
# so ConfigRepository.activate() (which archives siblings by tenant+kind+name) would
# never archive it - both would sit "active" side by side, and internal_policy() picks
# whichever is newest. Left uncleaned, that silently shadows the real seeded policy for
# every other test in the session, since tid/seeded are session-scoped. This fixture
# deletes anything this file creates under that name, whether the test passed or not.
@pytest.fixture
def _clean_test_policy(tid):
    from services.analytics_service.app import rules as bridge
    from cp_common.db import SessionLocal
    from sqlalchemy import text as sql

    yield
    db = SessionLocal()
    try:
        db.execute(sql("DELETE FROM config.tenant_configs WHERE tenant_id = :t "
                       "AND kind = 'policy' AND name = 'frm-policy-api-test'"),
                   {"t": tid})
        db.commit()
    finally:
        db.close()
    bridge.invalidate()


def test_a_policy_config_can_be_created_through_the_api(config_client, token_for, tid,
                                                         _clean_test_policy):
    h = token_for("tenant_admin")
    body = policy_body("urban_cooperative", 2)
    body["principal_officer_name"] = "Test Officer"
    r = config_client.post(f"/configs/{tid}", headers=h,
                           json={"kind": "policy", "name": "frm-policy-api-test",
                                 "version": "9.9.1", "body": body})
    assert r.status_code == 201, r.text
    assert r.json()["kind"] == "policy"


def test_activating_a_policy_version_without_attestation_is_refused(config_client,
                                                                     token_for, tid,
                                                                     _clean_test_policy):
    h = token_for("tenant_admin")
    body = policy_body("urban_cooperative", 2)
    r = config_client.post(f"/configs/{tid}", headers=h,
                           json={"kind": "policy", "name": "frm-policy-api-test",
                                 "version": "9.9.2", "body": body})
    config_id = r.json()["id"]
    r = config_client.post(f"/configs/{tid}/{config_id}/activate", headers=h)
    assert r.status_code == 422, r.text
    assert r.json()["error"]["code"] == "attestation_required"


def test_activating_an_attested_policy_version_reaches_analytics(config_client,
                                                                  token_for, tid,
                                                                  _clean_test_policy):
    from services.analytics_service.app import rules as bridge

    h = token_for("tenant_admin")
    body = policy_body("urban_cooperative", 2)
    body["principal_officer_name"] = "A. Console Test"
    body["attestation"] = {"approved_by": "risk_committee", "approved_on": "2026-08-01",
                           "reference": "RMC-TEST-001"}
    r = config_client.post(f"/configs/{tid}", headers=h,
                           json={"kind": "policy", "name": "frm-policy-api-test",
                                 "version": "9.9.3", "body": body})
    config_id = r.json()["id"]
    r = config_client.post(f"/configs/{tid}/{config_id}/activate", headers=h)
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "pending_activation"

    # BR-715: activation is maker-checker - a second, different eligible actor must
    # confirm before it actually reaches analytics.
    r = config_client.post(f"/configs/{tid}/{config_id}/activate",
                           headers=token_for("risk_manager"))
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "active"

    bridge.invalidate()
    pol = bridge.policy(tid, force=True)
    assert pol["principal_officer_name"] == "A. Console Test"
    assert pol["board_approved"] is True


def test_policy_unavailable_falls_back_without_claiming_authority(monkeypatch, tid):
    """If the control plane is unreachable we keep serving on documented defaults, but
    must not present them as the tenant's board-approved policy."""
    from services.analytics_service.app import rules as bridge

    class _Boom:
        @staticmethod
        def get(*a, **k):
            raise RuntimeError("config-service down")

    bridge.invalidate()
    monkeypatch.setattr(bridge, "httpx", _Boom)
    pol = bridge.policy(tid, force=True)
    assert pol["available"] is False
    assert pol["values"]["sla_breach_hours"] == 24   # documented default, not the UCB's 48
    bridge.invalidate()
