"""BR-316: a rail does not leave shadow mode without a recorded graduation basis.

Mirrors test_policy_attestation.py's weighting: mostly refusals, because a graduation
record that quietly passes incomplete is the whole failure mode - it would mean
enforcement turned on by a missed validation rather than a recorded decision.
"""
from datetime import date, timedelta

import pytest

from services.config_service.app import attestation, graduation

GOOD_RECORD = {
    "observation_window_days": 45,
    "false_positive_rate": 0.08,
    "evaluated_by": "R. Iyer, Fraud Risk Manager",
    "evaluated_on": "2026-08-01",
    "approved_by": "risk_committee",
    "approved_on": "2026-08-05",
    "reference": "RMC minute 2026/08/05 item 4",
}


def _body(rails=None, graduation_records=None):
    return {"version": "1.0.0",
            "rails": rails or {"UPI": {"mode": "inline", "shadow": False}},
            "graduation": graduation_records or {"UPI": GOOD_RECORD}}


# --------------------------------------------------------------- when it applies
def test_no_enforced_rails_needs_no_graduation_record():
    body = {"rails": {"UPI": {"mode": "inline", "shadow": True},
                      "NEFT": {"mode": "nrt"}}}
    assert graduation.validate(body) == {}


def test_a_shadow_rail_needs_no_record_even_if_present_elsewhere():
    body = {"rails": {"UPI": {"mode": "inline", "shadow": True}}}
    assert graduation.validate(body) == {}


def test_an_nrt_rail_marked_not_shadow_still_needs_no_record():
    """shadow=False only matters for a rail actually in the inline lane."""
    body = {"rails": {"NEFT": {"mode": "nrt", "shadow": False}}}
    assert graduation.validate(body) == {}


# --------------------------------------------------------------- it is required
def test_an_enforced_rail_with_no_graduation_record_is_refused():
    body = {"rails": {"UPI": {"mode": "inline", "shadow": False}}}
    with pytest.raises(graduation.GraduationError) as exc:
        graduation.validate(body)
    assert "UPI" in str(exc.value)


def test_only_the_enforced_rail_missing_a_record_is_named():
    body = _body(rails={"UPI": {"mode": "inline", "shadow": False},
                        "CARD": {"mode": "inline", "shadow": False}},
                graduation_records={"UPI": GOOD_RECORD})
    with pytest.raises(graduation.GraduationError) as exc:
        graduation.validate(body)
    message = str(exc.value)
    assert "CARD" in message
    # UPI has a complete record and must not be reported as missing one.
    missing_list = message.split(":", 1)[1].split(".")[0]
    assert "UPI" not in missing_list


@pytest.mark.parametrize("field", ["observation_window_days", "false_positive_rate",
                                   "evaluated_by", "evaluated_on", "approved_by",
                                   "approved_on", "reference"])
def test_each_required_field_is_named_when_missing(field):
    rec = {**GOOD_RECORD, field: ""}
    with pytest.raises(graduation.GraduationError) as exc:
        graduation.validate(_body(graduation_records={"UPI": rec}))
    assert field in str(exc.value)


# ------------------------------------------------------------- it is meaningful
def test_below_the_minimum_observation_window_is_refused():
    rec = {**GOOD_RECORD, "observation_window_days": 29}
    with pytest.raises(graduation.GraduationError) as exc:
        graduation.validate(_body(graduation_records={"UPI": rec}))
    assert "29" in str(exc.value)


def test_exactly_the_minimum_observation_window_is_accepted():
    rec = {**GOOD_RECORD, "observation_window_days": graduation.MIN_OBSERVATION_DAYS}
    out = graduation.validate(_body(graduation_records={"UPI": rec}))
    assert out["UPI"]["observation_window_days"] == graduation.MIN_OBSERVATION_DAYS


@pytest.mark.parametrize("rate", [-0.1, 1.1])
def test_a_false_positive_rate_outside_zero_to_one_is_refused(rate):
    rec = {**GOOD_RECORD, "false_positive_rate": rate}
    with pytest.raises(graduation.GraduationError):
        graduation.validate(_body(graduation_records={"UPI": rec}))


def test_an_unrecognised_approving_body_is_refused():
    rec = {**GOOD_RECORD, "approved_by": "the ops team"}
    with pytest.raises(graduation.GraduationError) as exc:
        graduation.validate(_body(graduation_records={"UPI": rec}))
    assert "not a recognised approving body" in str(exc.value)


def test_a_future_evaluated_on_is_refused():
    future = (date.today() + timedelta(days=5)).isoformat()
    rec = {**GOOD_RECORD, "evaluated_on": future}
    with pytest.raises(graduation.GraduationError):
        graduation.validate(_body(graduation_records={"UPI": rec}))


def test_a_future_approved_on_is_refused():
    future = (date.today() + timedelta(days=5)).isoformat()
    rec = {**GOOD_RECORD, "approved_on": future}
    with pytest.raises(graduation.GraduationError):
        graduation.validate(_body(graduation_records={"UPI": rec}))


def test_a_reference_too_short_to_follow_is_refused():
    rec = {**GOOD_RECORD, "reference": "ok"}
    with pytest.raises(graduation.GraduationError) as exc:
        graduation.validate(_body(graduation_records={"UPI": rec}))
    assert "too short" in str(exc.value)


def test_a_complete_record_is_normalised():
    out = graduation.validate(_body())
    assert out["UPI"]["observation_window_days"] == 45
    assert out["UPI"]["false_positive_rate"] == 0.08
    assert out["UPI"]["approved_by"] == "risk_committee"
    assert out["UPI"]["approved_by_label"] == "Risk Management Committee"
    assert out["UPI"]["reference"].startswith("RMC minute")


@pytest.mark.parametrize("key", sorted(attestation.APPROVING_BODIES))
def test_every_approving_body_recognised_by_attestation_is_accepted_here_too(key):
    """Graduation reuses attestation's own vocabulary rather than inventing a second
    one - a UCB's Board of Management should not need a different word here than it
    does for BR-104."""
    rec = {**GOOD_RECORD, "approved_by": key}
    out = graduation.validate(_body(graduation_records={"UPI": rec}))
    assert out["UPI"]["approved_by"] == key


def test_requires_graduation_applies_only_to_decision_policy():
    assert graduation.requires_graduation("decision_policy")
    for kind in ("rule", "typology", "network_map", "policy", "model"):
        assert not graduation.requires_graduation(kind)


# ============================================================ route-level (HTTP)
@pytest.fixture(autouse=True)
def _cleanup_decision_policy_configs(tid):
    yield
    from cp_common.db import SessionLocal
    from sqlalchemy import text as sql
    db = SessionLocal()
    try:
        db.execute(sql("DELETE FROM config.tenant_configs WHERE tenant_id = :t "
                       "AND kind = 'decision_policy'"), {"t": tid})
        db.commit()
    finally:
        db.close()


def _create_decision_policy(config_client, token_for, tid, *, role="tenant_admin",
                            name=None, body=None):
    import uuid
    name = name or f"dp-test-{uuid.uuid4().hex[:10]}"
    r = config_client.post(
        f"/configs/{tid}", headers=token_for(role),
        json={"kind": "decision_policy", "name": name, "version": "1.0.0",
              "body": body or {}})
    assert r.status_code == 201, r.text
    return r.json()


def test_activating_a_decision_policy_with_an_enforced_rail_and_no_record_is_refused(
        config_client, token_for, tid):
    cfg = _create_decision_policy(config_client, token_for, tid,
                                  body={"rails": {"UPI": {"mode": "inline",
                                                          "shadow": False}}})
    r = config_client.post(f"/configs/{tid}/{cfg['id']}/activate",
                           headers=token_for("tenant_admin"))
    assert r.status_code == 422, r.text
    assert r.json()["error"]["code"] == "graduation_required"


def test_a_decision_policy_with_every_rail_in_shadow_needs_no_graduation(
        config_client, token_for, tid):
    cfg = _create_decision_policy(config_client, token_for, tid,
                                  body={"rails": {"UPI": {"mode": "inline",
                                                          "shadow": True}}})
    r = config_client.post(f"/configs/{tid}/{cfg['id']}/activate",
                           headers=token_for("tenant_admin"))
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "pending_activation"


def test_a_fully_documented_decision_policy_still_needs_a_second_confirmer(
        config_client, token_for, tid):
    """BR-104-style graduation gates the proposal; BR-715 still gates the
    confirmation - paperwork alone does not make one person's activation enough to
    start enforcing a payment control."""
    cfg = _create_decision_policy(
        config_client, token_for, tid,
        body={"rails": {"UPI": {"mode": "inline", "shadow": False}},
              "graduation": {"UPI": GOOD_RECORD}})

    proposed = config_client.post(f"/configs/{tid}/{cfg['id']}/activate",
                                  headers=token_for("tenant_admin"))
    assert proposed.status_code == 200, proposed.text
    assert proposed.json()["status"] == "pending_activation"

    self_confirm = config_client.post(f"/configs/{tid}/{cfg['id']}/activate",
                                      headers=token_for("tenant_admin"))
    assert self_confirm.status_code == 403
    assert self_confirm.json()["error"]["code"] == "self_approval"

    confirmed = config_client.post(f"/configs/{tid}/{cfg['id']}/activate",
                                   headers=token_for("risk_manager"))
    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json()["status"] == "active"


def test_internal_decision_policy_endpoint_reports_unavailable_with_nothing_active(
        config_client, tid):
    from cp_common.settings import settings
    r = config_client.get(f"/internal/decision-policy/{tid}",
                          headers={"x-internal-key": settings.internal_api_key})
    assert r.status_code == 200, r.text
    assert r.json() == {"tenant_id": tid, "available": False}


def test_internal_decision_policy_endpoint_returns_the_active_body(
        config_client, token_for, tid):
    from cp_common.settings import settings

    cfg = _create_decision_policy(
        config_client, token_for, tid,
        body={"rails": {"UPI": {"mode": "inline", "shadow": False}},
              "graduation": {"UPI": GOOD_RECORD}})
    config_client.post(f"/configs/{tid}/{cfg['id']}/activate",
                       headers=token_for("tenant_admin"))
    config_client.post(f"/configs/{tid}/{cfg['id']}/activate",
                       headers=token_for("risk_manager"))

    r = config_client.get(f"/internal/decision-policy/{tid}",
                          headers={"x-internal-key": settings.internal_api_key})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["available"] is True
    assert body["body"]["rails"]["UPI"]["shadow"] is False


def test_internal_decision_policy_endpoint_requires_the_internal_key(
        config_client, tid):
    r = config_client.get(f"/internal/decision-policy/{tid}")
    assert r.status_code == 401, r.text
