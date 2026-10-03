"""BR-715: maker-checker on privileged configuration changes.

Activating a rule or policy version changes what live decisioning actually blocks, so
one person's word for it is not enough - the same discipline the RFA case lifecycle
already applies to ``declare_fraud`` (see ``analytics_service.app.workflow``). These
tests are weighted toward the refusals, because a proposal that quietly self-activates
is the whole failure mode.
"""
import pytest

ELIGIBLE = ("tenant_admin", "risk_manager", "platform_admin")
INELIGIBLE = ("analyst", "investigator", "supervisor", "board")


# tid/seeded are session-scoped, so any config this file activates outlives the test
# that created it. An activated "rule" would join the tenant's real rule catalogue
# (internal_active_rules has no name filter) and an activated "policy" would shadow the
# seeded "frm-policy" the same way test_policy.py's _clean_test_policy fixture already
# guards against - see its docstring. This is that same guard, applied to every test
# in this module rather than one at a time.
@pytest.fixture(autouse=True)
def _cleanup_mc_configs(tid):
    yield
    from cp_common.db import SessionLocal
    from sqlalchemy import text as sql
    db = SessionLocal()
    try:
        db.execute(sql("DELETE FROM config.tenant_configs WHERE tenant_id = :t "
                       "AND (name LIKE 'mc-test-%' OR name LIKE 'mc-sibling-%')"),
                   {"t": tid})
        db.commit()
    finally:
        db.close()
    from services.analytics_service.app import rules as bridge
    bridge.invalidate()


def _create(config_client, token_for, tid, *, role="risk_manager", kind="rule",
            name=None, body=None):
    import uuid
    name = name or f"mc-test-{uuid.uuid4().hex[:10]}"
    r = config_client.post(
        f"/configs/{tid}", headers=token_for(role),
        json={"kind": kind, "name": name, "version": "1.0.0", "body": body or {"x": 1}})
    assert r.status_code == 201, r.text
    return r.json()


def _activate(config_client, token_for, tid, config_id, role):
    return config_client.post(
        f"/configs/{tid}/{config_id}/activate", headers=token_for(role))


def _reject(config_client, token_for, tid, config_id, role):
    return config_client.post(
        f"/configs/{tid}/{config_id}/reject-activation", headers=token_for(role))


# --------------------------------------------------------------- the happy path
def test_first_activate_call_proposes_but_does_not_activate(config_client, token_for, tid):
    cfg = _create(config_client, token_for, tid)
    r = _activate(config_client, token_for, tid, cfg["id"], "risk_manager")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "pending_activation"
    assert body["proposed_by_role"] == "risk_manager"
    assert body["approved_by"] is None


def test_second_call_by_a_different_eligible_role_activates_it(config_client, token_for, tid):
    cfg = _create(config_client, token_for, tid)
    _activate(config_client, token_for, tid, cfg["id"], "risk_manager")
    r = _activate(config_client, token_for, tid, cfg["id"], "tenant_admin")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "active"
    assert body["proposed_by_role"] == "risk_manager"
    assert body["approved_by_role"] == "tenant_admin"


# --------------------------------------------------------- the guard it exists for
def test_the_same_actor_cannot_confirm_their_own_proposal(config_client, token_for, tid):
    cfg = _create(config_client, token_for, tid)
    _activate(config_client, token_for, tid, cfg["id"], "risk_manager")
    r = _activate(config_client, token_for, tid, cfg["id"], "risk_manager")
    assert r.status_code == 403, r.text
    assert r.json()["error"]["code"] == "self_approval"
    # And it must still be sitting there unconfirmed, not silently activated.
    listed = config_client.get(f"/configs/{tid}", headers=token_for("risk_manager")).json()
    row = next(c for c in listed if c["id"] == cfg["id"])
    assert row["status"] == "pending_activation"


def test_two_different_people_holding_the_same_role_still_works(config_client, token_for, tid):
    """The guard is 'different actor', not 'different role' - two risk managers is fine."""
    # This suite only seeds one user per role, so this documents the intended semantics
    # via the unit-level guard directly rather than needing a second seeded account.
    from services.config_service.app import maker_checker

    class Row:
        proposed_by = "risk_manager@test.local"

    # Eligibility is read from the tenant's own role row, so this needs the real tenant.
    maker_checker.confirm(tid, Row(), "another_risk_manager@test.local", "risk_manager")
    with pytest.raises(maker_checker.MakerCheckerRefused):
        maker_checker.confirm(tid, Row(), "risk_manager@test.local", "risk_manager")


# ----------------------------------------------------------- who may act at all
@pytest.mark.parametrize("role", INELIGIBLE)
def test_an_ineligible_role_cannot_propose(config_client, token_for, tid, role):
    cfg = _create(config_client, token_for, tid)
    r = _activate(config_client, token_for, tid, cfg["id"], role)
    assert r.status_code == 403, r.text
    assert r.json()["error"]["code"] == "role_not_permitted"


def test_an_ineligible_role_cannot_confirm_either(config_client, token_for, tid):
    cfg = _create(config_client, token_for, tid)
    _activate(config_client, token_for, tid, cfg["id"], "risk_manager")
    r = _activate(config_client, token_for, tid, cfg["id"], "analyst")
    assert r.status_code == 403, r.text
    assert r.json()["error"]["code"] == "role_not_permitted"
    listed = config_client.get(f"/configs/{tid}", headers=token_for("risk_manager")).json()
    row = next(c for c in listed if c["id"] == cfg["id"])
    assert row["status"] == "pending_activation", \
        "an ineligible confirmer must not be able to move this to active"


# -------------------------------------------------------------------- rejection
def test_a_pending_proposal_can_be_rejected_back_to_draft(config_client, token_for, tid):
    cfg = _create(config_client, token_for, tid)
    _activate(config_client, token_for, tid, cfg["id"], "risk_manager")
    r = _reject(config_client, token_for, tid, cfg["id"], "tenant_admin")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "draft"
    assert body["proposed_by"] is None


def test_the_proposer_may_withdraw_their_own_proposal(config_client, token_for, tid):
    """Withdrawing is not self-approval - nothing takes effect either way."""
    cfg = _create(config_client, token_for, tid)
    _activate(config_client, token_for, tid, cfg["id"], "risk_manager")
    r = _reject(config_client, token_for, tid, cfg["id"], "risk_manager")
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "draft"


def test_a_draft_that_was_never_proposed_cannot_be_rejected(config_client, token_for, tid):
    cfg = _create(config_client, token_for, tid)
    r = _reject(config_client, token_for, tid, cfg["id"], "tenant_admin")
    assert r.status_code == 409, r.text
    assert r.json()["error"]["code"] == "not_pending"


def test_after_rejection_the_cycle_can_start_again(config_client, token_for, tid):
    cfg = _create(config_client, token_for, tid)
    _activate(config_client, token_for, tid, cfg["id"], "risk_manager")
    _reject(config_client, token_for, tid, cfg["id"], "tenant_admin")
    r1 = _activate(config_client, token_for, tid, cfg["id"], "tenant_admin")
    assert r1.json()["status"] == "pending_activation"
    r2 = _activate(config_client, token_for, tid, cfg["id"], "risk_manager")
    assert r2.status_code == 200, r2.text
    assert r2.json()["status"] == "active"


# ---------------------------------------------------------- terminal states
def test_an_already_active_version_cannot_be_activated_again(config_client, token_for, tid):
    cfg = _create(config_client, token_for, tid)
    _activate(config_client, token_for, tid, cfg["id"], "risk_manager")
    _activate(config_client, token_for, tid, cfg["id"], "tenant_admin")
    r = _activate(config_client, token_for, tid, cfg["id"], "risk_manager")
    assert r.status_code == 409, r.text
    assert r.json()["error"]["code"] == "already_active"


def test_activating_a_second_version_archives_the_first(config_client, token_for, tid):
    import uuid
    name = f"mc-sibling-{uuid.uuid4().hex[:10]}"
    v1 = _create(config_client, token_for, tid, name=name, body={"x": 1})
    _activate(config_client, token_for, tid, v1["id"], "risk_manager")
    _activate(config_client, token_for, tid, v1["id"], "tenant_admin")

    r = config_client.post(
        f"/configs/{tid}", headers=token_for("risk_manager"),
        json={"kind": "rule", "name": name, "version": "2.0.0", "body": {"x": 2}})
    v2 = r.json()
    _activate(config_client, token_for, tid, v2["id"], "risk_manager")
    _activate(config_client, token_for, tid, v2["id"], "tenant_admin")

    listed = config_client.get(f"/configs/{tid}", headers=token_for("risk_manager")).json()
    by_id = {c["id"]: c for c in listed}
    assert by_id[v1["id"]]["status"] == "archived"
    assert by_id[v2["id"]]["status"] == "active"


# ------------------------------------------------------- BR-104 stays intact
def test_a_policy_still_needs_attestation_before_it_can_even_be_proposed(
        config_client, token_for, tid):
    cfg = _create(config_client, token_for, tid, kind="policy",
                  body={"thresholds": {"fmr_filing_days": 7}})
    r = _activate(config_client, token_for, tid, cfg["id"], "tenant_admin")
    assert r.status_code == 422, r.text
    assert r.json()["error"]["code"] == "attestation_required"


def test_an_attested_policy_still_needs_a_second_confirmer(config_client, token_for, tid):
    """BR-104 and BR-715 stack: attestation gets it proposed, a different eligible actor
    still has to confirm before it is the tenant's active policy."""
    cfg = _create(config_client, token_for, tid, kind="policy", body={
        "thresholds": {"fmr_filing_days": 7},
        "attestation": {"approved_by": "board", "approved_on": "2026-03-14",
                         "reference": "Board minute 2026/03/14 item 7"},
    })
    proposed = _activate(config_client, token_for, tid, cfg["id"], "tenant_admin")
    assert proposed.status_code == 200, proposed.text
    assert proposed.json()["status"] == "pending_activation"

    confirmed = _activate(config_client, token_for, tid, cfg["id"], "risk_manager")
    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json()["status"] == "active"
