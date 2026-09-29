"""AI/ML roadmap Phase 2: the trained artifact behind a ``model`` config version (BRD
OD-06/s19; HLD AD-14/s16).

Same shape as test_model_governance.py's challenger tests - a model version that passes
its evaluation and committee approval but names no loadable artifact is a version that
can be activated and never actually score anything, which nobody would notice until an
inspection asked what code produced a given alert. These exercise the gate directly, the
same way test_model_governance.py exercises BR-807/808.
"""
import pytest

from services.config_service.app import model_artifact

GOOD_ARTIFACT = {
    "format": "sklearn-joblib",
    "uri": "file:///var/models/velocity_anomaly_v1.joblib",
    "sha256": "e84ca5d7f3961345cd30510743769986981478df8f16e056775fa77b7580e152",
}


def body(**over):
    art = {**GOOD_ARTIFACT, **over}
    return {"model_artifact": art}


# ------------------------------------------------------------- it is required
def test_a_model_with_no_artifact_is_refused():
    with pytest.raises(model_artifact.ModelArtifactError) as exc:
        model_artifact.validate({})
    assert "no trained artifact recorded" in str(exc.value)


def test_an_empty_artifact_is_refused_not_treated_as_absent_but_fine():
    with pytest.raises(model_artifact.ModelArtifactError):
        model_artifact.validate({"model_artifact": {}})


@pytest.mark.parametrize("field", ["format", "uri", "sha256"])
def test_each_required_field_is_named_when_missing(field):
    with pytest.raises(model_artifact.ModelArtifactError) as exc:
        model_artifact.validate(body(**{field: ""}))
    assert field in str(exc.value)


# ----------------------------------------------------------- it is meaningful
def test_an_unsupported_format_is_refused():
    with pytest.raises(model_artifact.ModelArtifactError) as exc:
        model_artifact.validate(body(format="pickle"))
    assert "pickle" in str(exc.value) and "sklearn-joblib" in str(exc.value)


def test_onnx_is_named_as_a_future_format_not_a_silent_pass():
    """Declared in the roadmap as a future option - nothing loads it yet, so declaring
    an artifact in that format must be refused, not silently accepted and then fail
    unexplained the first time a batch tries to score with it."""
    with pytest.raises(model_artifact.ModelArtifactError):
        model_artifact.validate(body(format="onnx"))


def test_graph_embedding_isolation_forest_is_a_supported_format():
    """AI/ML roadmap Phase 3 (AD-15) - one artifact carrying both the GraphSAGE encoder
    and the IsolationForest that scores its embeddings."""
    got = model_artifact.validate(body(format="graph-embedding-isolation-forest"))
    assert got["format"] == "graph-embedding-isolation-forest"


@pytest.mark.parametrize("bad_sha", [
    "not-hex-at-all-but-64-characters-long-so-length-check-alone-would-miss-it!!",
    "abc123",             # too short
    "g" * 64,             # right length, not hex
])
def test_a_malformed_sha256_is_refused(bad_sha):
    with pytest.raises(model_artifact.ModelArtifactError) as exc:
        model_artifact.validate(body(sha256=bad_sha))
    assert "sha256" in str(exc.value)


def test_a_complete_artifact_is_normalised():
    got = model_artifact.validate(body())
    assert got["format"] == "sklearn-joblib"
    assert got["uri"] == GOOD_ARTIFACT["uri"]
    assert got["sha256"] == GOOD_ARTIFACT["sha256"]


def test_format_and_sha256_are_case_normalised():
    got = model_artifact.validate(body(format="SKLEARN-JOBLIB",
                                       sha256=GOOD_ARTIFACT["sha256"].upper()))
    assert got["format"] == "sklearn-joblib"
    assert got["sha256"] == GOOD_ARTIFACT["sha256"]


# ------------------------------------------------------- it applies to model only
def test_only_the_model_kind_needs_an_artifact():
    assert model_artifact.requires_artifact("model")
    for kind in ("rule", "typology", "network_map", "policy", "decision_policy"):
        assert not model_artifact.requires_artifact(kind), (
            f"{kind} must not need a trained artifact - there is nothing for a "
            "deterministic rule or policy config to load")
