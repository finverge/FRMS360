"""BR-807/808: a candidate model needs a recorded champion/challenger evaluation and a
model risk committee's approval before it may even be proposed for activation - not
approved on its own numbers, and not on the operator's say-so. A third gate,
``model_artifact`` (AI/ML roadmap Phase 2, HLD AD-14/s16), additionally requires a
loadable trained artifact reference - see test_model_artifact.py.

Fraud360 now has live ML scoring (AI/ML roadmap Phase 2, BRD s19): a tenant that clears
all three gates and confirms activation has a model genuinely loaded and scoring VEL-04
in the detection worker (services/analytics_service/app/detection/model_scoring.py).
These tests still exercise the gates directly against the ``model`` config kind, the
same way test_policy_attestation.py exercises BR-104 against ``policy`` - the route-level
tests below cover the full propose/confirm flow; model_scoring's own tests cover
inference.
"""
from datetime import date, timedelta

import pytest

from services.config_service.app import challenger

GOOD_EVAL = {
    "champion_version": "1.0.0",
    "evaluated_by": "A. Rao, Model Risk",
    "evaluated_on": "2026-08-01",
    "method": "Backtest replay over 90 days of live traffic (BR-806 harness)",
    "metrics": {"precision": 0.87, "recall": 0.79, "false_positive_rate": 0.02},
    "comparison_summary": "Challenger improves recall 6pp over champion at equal precision.",
}


def body(**over):
    ev = {**GOOD_EVAL, **over}
    return {"challenger_evaluation": ev}


# ------------------------------------------------------------- it is required
def test_a_model_without_an_evaluation_is_refused():
    with pytest.raises(challenger.ChallengerEvaluationError) as exc:
        challenger.validate({})
    assert "no champion/challenger evaluation recorded" in str(exc.value)


def test_an_empty_evaluation_is_refused_not_treated_as_absent_but_fine():
    with pytest.raises(challenger.ChallengerEvaluationError):
        challenger.validate({"challenger_evaluation": {}})


@pytest.mark.parametrize("field", ["evaluated_by", "evaluated_on", "method",
                                   "comparison_summary"])
def test_each_required_text_field_is_named_when_missing(field):
    with pytest.raises(challenger.ChallengerEvaluationError) as exc:
        challenger.validate(body(**{field: ""}))
    assert field in str(exc.value)


def test_metrics_missing_is_named_as_missing():
    ev = dict(GOOD_EVAL)
    del ev["metrics"]
    with pytest.raises(challenger.ChallengerEvaluationError) as exc:
        challenger.validate({"challenger_evaluation": ev})
    assert "metrics" in str(exc.value)


def test_champion_version_is_optional_for_a_first_model():
    """No prior champion is a legitimate state - the comparison_summary is expected to
    say so, not the champion_version field."""
    got = challenger.validate(body(champion_version=""))
    assert got["champion_version"] == ""


# ----------------------------------------------------------- it is meaningful
def test_metrics_must_be_a_non_empty_mapping():
    """An empty metrics dict is caught as a missing required field - same message
    family as any other blank field, not a separate silent pass."""
    with pytest.raises(challenger.ChallengerEvaluationError) as exc:
        challenger.validate(body(metrics={}))
    assert "metrics" in str(exc.value)


def test_every_metric_value_must_be_numeric():
    with pytest.raises(challenger.ChallengerEvaluationError) as exc:
        challenger.validate(body(metrics={"precision": "high"}))
    assert "must be a number" in str(exc.value)


def test_a_future_evaluation_date_is_refused():
    future = (date.today() + timedelta(days=10)).isoformat()
    with pytest.raises(challenger.ChallengerEvaluationError) as exc:
        challenger.validate(body(evaluated_on=future))
    assert "in the future" in str(exc.value)


def test_an_unparseable_evaluation_date_is_refused():
    with pytest.raises(challenger.ChallengerEvaluationError):
        challenger.validate(body(evaluated_on="last tuesday"))


def test_a_method_too_short_to_be_useful_is_refused():
    with pytest.raises(challenger.ChallengerEvaluationError) as exc:
        challenger.validate(body(method="ran it"))
    assert "too short" in str(exc.value)


def test_a_summary_too_short_to_be_useful_is_refused():
    with pytest.raises(challenger.ChallengerEvaluationError) as exc:
        challenger.validate(body(comparison_summary="better"))
    assert "too short" in str(exc.value)


def test_a_complete_evaluation_is_normalised():
    got = challenger.validate(body())
    assert got["champion_version"] == "1.0.0"
    assert got["evaluated_by"] == "A. Rao, Model Risk"
    assert got["evaluated_on"] == "2026-08-01"
    assert got["metrics"]["precision"] == 0.87
    assert got["metrics"]["recall"] == 0.79
    assert "Challenger improves recall" in got["comparison_summary"]


# ------------------------------------------------------- it applies to model only
def test_only_the_model_kind_needs_an_evaluation():
    assert challenger.requires_evaluation("model")
    for kind in ("rule", "typology", "network_map", "policy"):
        assert not challenger.requires_evaluation(kind), (
            f"{kind} must not need a champion/challenger record - there is no model "
            "to compare for a deterministic rule or policy config")


# ============================================================ route-level (HTTP)
# tid/seeded are session-scoped; any "model" kind config this file activates would
# outlive the test that created it, and detection's own model-scoring step now reads
# an active "model" config (internal_active_model) for every tenant it evaluates.
# Cleaned up anyway, on the same principle as test_config_maker_checker.py's guard -
# and so a leftover active model from this file never scores a later test's traffic.
@pytest.fixture(autouse=True)
def _cleanup_model_configs(tid):
    yield
    from cp_common.db import SessionLocal
    from sqlalchemy import text as sql
    db = SessionLocal()
    try:
        db.execute(sql("DELETE FROM config.tenant_configs WHERE tenant_id = :t "
                       "AND kind = 'model'"), {"t": tid})
        db.commit()
    finally:
        db.close()


def _create_model(config_client, token_for, tid, *, role="tenant_admin", name=None,
                  body=None):
    import uuid
    name = name or f"model-test-{uuid.uuid4().hex[:10]}"
    r = config_client.post(
        f"/configs/{tid}", headers=token_for(role),
        json={"kind": "model", "name": name, "version": "1.0.0", "body": body or {}})
    assert r.status_code == 201, r.text
    return r.json()


def test_activating_a_model_with_neither_evaluation_nor_attestation_is_refused(
        config_client, token_for, tid):
    cfg = _create_model(config_client, token_for, tid)
    r = config_client.post(f"/configs/{tid}/{cfg['id']}/activate",
                           headers=token_for("tenant_admin"))
    assert r.status_code == 422, r.text
    # Attestation is checked first - whichever gate reports first, it must be one of the
    # two real gates, not a silent pass.
    assert r.json()["error"]["code"] in ("attestation_required",
                                         "challenger_evaluation_required")


def test_a_model_with_attestation_but_no_evaluation_is_refused(config_client, token_for,
                                                                tid):
    cfg = _create_model(config_client, token_for, tid, body={
        "attestation": {"approved_by": "model_risk_committee", "approved_on": "2026-08-01",
                        "reference": "MRC minute 2026/08/01 item 3"},
    })
    r = config_client.post(f"/configs/{tid}/{cfg['id']}/activate",
                           headers=token_for("tenant_admin"))
    assert r.status_code == 422, r.text
    assert r.json()["error"]["code"] == "challenger_evaluation_required"


def test_a_model_with_evaluation_but_no_attestation_is_refused(config_client, token_for,
                                                                tid):
    cfg = _create_model(config_client, token_for, tid, body={
        "challenger_evaluation": GOOD_EVAL,
    })
    r = config_client.post(f"/configs/{tid}/{cfg['id']}/activate",
                           headers=token_for("tenant_admin"))
    assert r.status_code == 422, r.text
    assert r.json()["error"]["code"] == "attestation_required"


GOOD_ARTIFACT = {
    "format": "sklearn-joblib",
    "uri": "file:///var/models/velocity_anomaly_v1.joblib",
    "sha256": "e84ca5d7f3961345cd30510743769986981478df8f16e056775fa77b7580e152",
}


def test_a_model_with_attestation_and_evaluation_but_no_artifact_is_refused(
        config_client, token_for, tid):
    """AI/ML roadmap Phase 2 (HLD AD-14): the third gate. A model that passes both
    governance checks but names nothing loadable is a version that activates and never
    scores anything - refused for the same reason a blank evaluation is."""
    cfg = _create_model(config_client, token_for, tid, body={
        "attestation": {"approved_by": "model_risk_committee", "approved_on": "2026-08-01",
                        "reference": "MRC minute 2026/08/01 item 3"},
        "challenger_evaluation": GOOD_EVAL,
    })
    r = config_client.post(f"/configs/{tid}/{cfg['id']}/activate",
                           headers=token_for("tenant_admin"))
    assert r.status_code == 422, r.text
    assert r.json()["error"]["code"] == "model_artifact_required"


def test_a_fully_documented_model_still_needs_a_second_confirmer(config_client,
                                                                  token_for, tid):
    """BR-104/BR-807/BR-808/AD-14 gate the proposal; BR-715 still gates the
    confirmation. Passing the paperwork does not make one person's activation
    sufficient."""
    cfg = _create_model(config_client, token_for, tid, body={
        "attestation": {"approved_by": "model_risk_committee", "approved_on": "2026-08-01",
                        "reference": "MRC minute 2026/08/01 item 3"},
        "challenger_evaluation": GOOD_EVAL,
        "model_artifact": GOOD_ARTIFACT,
    })
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


# ------------------------- /internal/model - what detection's worker actually reads
def test_internal_model_endpoint_reports_unavailable_with_nothing_active(
        config_client, tid):
    from cp_common.settings import settings
    r = config_client.get(f"/internal/model/{tid}/velocity-anomaly",
                          headers={"x-internal-key": settings.internal_api_key})
    assert r.status_code == 200, r.text
    assert r.json() == {"tenant_id": tid, "name": "velocity-anomaly", "available": False}


def test_internal_model_endpoint_returns_the_active_artifact_reference(
        config_client, token_for, tid):
    from cp_common.settings import settings

    cfg = _create_model(config_client, token_for, tid, name="velocity-anomaly", body={
        "attestation": {"approved_by": "model_risk_committee", "approved_on": "2026-08-01",
                        "reference": "MRC minute 2026/08/01 item 3"},
        "challenger_evaluation": GOOD_EVAL,
        "model_artifact": GOOD_ARTIFACT,
    })
    config_client.post(f"/configs/{tid}/{cfg['id']}/activate",
                       headers=token_for("tenant_admin"))
    config_client.post(f"/configs/{tid}/{cfg['id']}/activate",
                       headers=token_for("risk_manager"))

    r = config_client.get(f"/internal/model/{tid}/velocity-anomaly",
                          headers={"x-internal-key": settings.internal_api_key})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["available"] is True
    assert body["body"]["model_artifact"]["uri"] == GOOD_ARTIFACT["uri"]
    assert body["body"]["model_artifact"]["sha256"] == GOOD_ARTIFACT["sha256"]


def test_internal_model_endpoint_is_scoped_by_name_not_just_tenant(
        config_client, token_for, tid):
    """The gap Phase 3 exposed: two differently-named model versions can be active for
    the same tenant at once, and each must be reachable only by its own name - not by
    whichever happens to come back first for the tenant."""
    from cp_common.settings import settings

    cfg = _create_model(config_client, token_for, tid, name="velocity-anomaly", body={
        "attestation": {"approved_by": "model_risk_committee", "approved_on": "2026-08-01",
                        "reference": "MRC minute 2026/08/01 item 3"},
        "challenger_evaluation": GOOD_EVAL,
        "model_artifact": GOOD_ARTIFACT,
    })
    config_client.post(f"/configs/{tid}/{cfg['id']}/activate",
                       headers=token_for("tenant_admin"))
    config_client.post(f"/configs/{tid}/{cfg['id']}/activate",
                       headers=token_for("risk_manager"))

    # A different name, never activated, must report unavailable even though this
    # tenant genuinely has an active model under a different name.
    r = config_client.get(f"/internal/model/{tid}/graph-ring-score",
                          headers={"x-internal-key": settings.internal_api_key})
    assert r.status_code == 200, r.text
    assert r.json() == {"tenant_id": tid, "name": "graph-ring-score", "available": False}

    # The one that was actually activated is still reachable by its own name.
    r = config_client.get(f"/internal/model/{tid}/velocity-anomaly",
                          headers={"x-internal-key": settings.internal_api_key})
    assert r.json()["available"] is True


def test_internal_model_endpoint_requires_the_internal_key(config_client, tid):
    r = config_client.get(f"/internal/model/{tid}/velocity-anomaly")
    assert r.status_code == 401, r.text
