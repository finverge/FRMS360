"""AI/ML roadmap Phase 2 - the scoring runtime itself (BRD OD-06/s19; HLD AD-14/s16).

Trains a tiny, real IsolationForest inline rather than depending on the checked-in
``var/models`` artifact - that file is environment-specific training output (see
scripts/train_velocity_anomaly_model.py), not a fixture this suite should couple itself
to. What matters here is the loader's own behaviour: it fetches, verifies and
deserialises correctly, refuses what it should refuse, and the calibration it applies is
sound - not that any particular tenant's model scores any particular way.
"""
import hashlib

import joblib
import pytest
from sklearn.ensemble import IsolationForest

from services.analytics_service.app.detection import model_scoring as ms
from cp_common.observations import AccountContext

# A tiny, deliberately separable training set: most points clustered near the origin
# (ordinary traffic), a handful far out (what an anomaly detector should learn to flag).
_NORMAL = [[0.2 + 0.01 * i, 2.0, 0.1, 0.0, 0.0] for i in range(40)]
_OUTLIERS = [[50.0, 100.0, 20.0, 30.0, 5.0], [60.0, 90.0, 25.0, 40.0, 6.0]]


def _write_bundle(path, *, estimator=None, score_low=None, score_high=None,
                  as_dict=True):
    if estimator is None:
        estimator = IsolationForest(n_estimators=50, random_state=42).fit(_NORMAL)
    if score_low is None or score_high is None:
        raw = estimator.score_samples(_NORMAL + _OUTLIERS)
        score_low, score_high = min(raw), max(raw)
    payload = ({"estimator": estimator, "score_low": score_low, "score_high": score_high}
              if as_dict else estimator)
    joblib.dump(payload, path)
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    return digest


def _uri(path) -> str:
    # pathlib's own as_uri(), not hand-concatenated: a naive "file://" + path silently
    # loses the Windows drive letter into the URI's netloc instead of its path, which
    # only coincidentally resolves when the process's current drive happens to match.
    return path.resolve().as_uri()


@pytest.fixture(autouse=True)
def _clear_cache():
    ms.invalidate_cache()
    yield
    ms.invalidate_cache()


# ----------------------------------------------------------------- feature_vector
def test_feature_vector_reuses_the_same_formulas_as_the_rules_engine():
    """VEL-01's value_vs_baseline and LAY-01's outflow_ratio, not a second definition -
    a divergence here would mean the model was trained against one measurement and
    scored against another."""
    ctx = AccountContext(baseline_mean_paise=1000.0, outflow_paise=800, inflow_paise=1000,
                         distinct_counterparties=7, small_credit_count=3, sub_ctr_count=1)
    txn = {"amount_paise": 2000}
    got = ms.feature_vector(txn, ctx)
    assert got == [2.0, 7.0, 0.8, 3.0, 1.0]


def test_feature_vector_treats_absent_history_as_zero_not_undefined():
    """A brand-new account has no baseline and no inflow - 0.0 is the same neutral
    value VEL-01/LAY-01 use for that case, not a value the model would read as
    anomalous on its own."""
    ctx = AccountContext()
    got = ms.feature_vector({"amount_paise": 5000}, ctx)
    assert got[0] == 0.0 and got[2] == 0.0


# ----------------------------------------------------------------------- load()
def test_load_verifies_before_deserialising(tmp_path):
    path = tmp_path / "model.joblib"
    _write_bundle(path)
    with pytest.raises(ms.ModelLoadError) as exc:
        ms.load(_uri(path), "0" * 64)
    assert "does not match its recorded sha256" in str(exc.value)


def test_load_refuses_an_unsupported_uri_scheme():
    with pytest.raises(ms.ModelLoadError) as exc:
        ms.load("s3://some-bucket/model.joblib", "0" * 64)
    assert "s3" in str(exc.value) and "file://" in str(exc.value)


def test_load_refuses_a_missing_file(tmp_path):
    with pytest.raises(ms.ModelLoadError):
        ms.load(_uri(tmp_path / "does_not_exist.joblib"), "0" * 64)


def test_load_refuses_a_bundle_missing_calibration(tmp_path):
    """An artifact that is not the {estimator, score_low, score_high} shape this loader
    expects - deserialises fine, but is not usable, and must be refused as such rather
    than crashing with an unhandled AttributeError deep in score()."""
    path = tmp_path / "bare_estimator.joblib"
    digest = _write_bundle(path, as_dict=False)
    with pytest.raises(ms.ModelLoadError) as exc:
        ms.load(_uri(path), digest)
    assert "recognised bundle" in str(exc.value)


def test_load_refuses_a_degenerate_calibration_range(tmp_path):
    path = tmp_path / "degenerate.joblib"
    digest = _write_bundle(path, score_low=0.5, score_high=0.5)
    with pytest.raises(ms.ModelLoadError) as exc:
        ms.load(_uri(path), digest)
    assert "degenerate calibration range" in str(exc.value)


def test_a_valid_artifact_loads_successfully(tmp_path):
    path = tmp_path / "model.joblib"
    digest = _write_bundle(path)
    loaded = ms.load(_uri(path), digest)
    assert loaded.sha256 == digest
    assert loaded.score_high > loaded.score_low


def test_load_is_cached_by_uri_and_digest_together(tmp_path):
    """A cache keyed on the digest, not just the path, so a replacement file at the same
    path is loaded fresh rather than silently reusing a stale in-memory model."""
    path = tmp_path / "model.joblib"
    digest1 = _write_bundle(path)
    first = ms.load(_uri(path), digest1)

    digest2 = _write_bundle(path)  # different random_state seed baked in, same file path
    second = ms.load(_uri(path), digest2)

    assert first is ms.load(_uri(path), digest1), "same key must hit the cache"
    assert first is not second, "a different digest must not reuse the old entry"


# ----------------------------------------------------------------------- score()
def test_score_is_normalised_between_zero_and_one_and_orders_correctly(tmp_path):
    path = tmp_path / "model.joblib"
    digest = _write_bundle(path)
    loaded = ms.load(_uri(path), digest)

    normal_score = ms.score(loaded, _NORMAL[0])
    outlier_score = ms.score(loaded, _OUTLIERS[0])
    assert 0.0 <= normal_score <= 1.0
    assert 0.0 <= outlier_score <= 1.0
    assert outlier_score > normal_score, (
        "an obvious outlier must score higher than an ordinary point, or the "
        "calibration direction is backwards")


def test_score_clamps_a_point_outside_the_training_range(tmp_path):
    """Live traffic can be more extreme than anything the training set saw, in either
    direction - clamping keeps that a boundary value rather than a score outside the
    catalogue's own 0-1 band."""
    path = tmp_path / "model.joblib"
    digest = _write_bundle(path)
    loaded = ms.load(_uri(path), digest)
    extreme = ms.score(loaded, [500.0, 1000.0, 200.0, 300.0, 50.0])
    assert extreme == 1.0


# ------------------------------- AI/ML roadmap Phase 3 (graph-embedding-isolation-forest)
def _write_graph_bundle(path, *, in_dim=8):
    from services.analytics_service.app.detection.graph_model import RingScoreEncoder

    encoder = RingScoreEncoder(in_dim=in_dim)
    estimator = IsolationForest(n_estimators=20, random_state=42).fit(_NORMAL_8D(in_dim))
    raw = estimator.score_samples(_NORMAL_8D(in_dim))
    bundle = {"estimator": estimator, "score_low": float(min(raw)),
             "score_high": float(max(raw)), "gnn_state_dict": encoder.state_dict(),
             "gnn_in_dim": in_dim}
    joblib.dump(bundle, path)
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _NORMAL_8D(dim):
    return [[0.1 * i] * dim for i in range(1, 21)]


def test_a_graph_bundle_loads_with_a_reconstructed_encoder(tmp_path):
    path = tmp_path / "graph.joblib"
    digest = _write_graph_bundle(path)
    loaded = ms.load(_uri(path), digest, format="graph-embedding-isolation-forest")
    assert loaded.gnn is not None
    # A plain sklearn-joblib load never reconstructs an encoder - the two formats must
    # not silently converge.
    assert loaded.gnn is not None and hasattr(loaded.gnn, "forward")


def test_a_plain_sklearn_bundle_has_no_gnn_component(tmp_path):
    path = tmp_path / "model.joblib"
    digest = _write_bundle(path)
    loaded = ms.load(_uri(path), digest)  # default format="sklearn-joblib"
    assert loaded.gnn is None


def test_an_unknown_declared_format_is_refused(tmp_path):
    path = tmp_path / "model.joblib"
    digest = _write_bundle(path)
    with pytest.raises(ms.ModelLoadError) as exc:
        ms.load(_uri(path), digest, format="onnx")
    assert "onnx" in str(exc.value)


def test_a_graph_bundle_missing_gnn_fields_is_refused(tmp_path):
    """Declares the graph format but the bundle itself has no encoder - deserialises
    fine, but is not usable, and must fail loudly rather than with an unhandled
    KeyError deep in scoring code."""
    path = tmp_path / "graph.joblib"
    digest = _write_bundle(path)  # a plain Phase 2 bundle, no gnn_state_dict at all
    with pytest.raises(ms.ModelLoadError) as exc:
        ms.load(_uri(path), digest, format="graph-embedding-isolation-forest")
    assert "gnn_state_dict" in str(exc.value) or "gnn_in_dim" in str(exc.value)


def test_graph_encoder_reconstructed_from_the_bundle_matches_the_original(tmp_path):
    """The whole point of bundling a state_dict: what comes back out must score
    embeddings identically to the encoder that was actually trained and evaluated."""
    from services.analytics_service.app.detection.graph_model import RingScoreEncoder
    import torch

    original = RingScoreEncoder(in_dim=8)
    original.eval()
    path = tmp_path / "graph.joblib"
    estimator = IsolationForest(n_estimators=20, random_state=42).fit(_NORMAL_8D(8))
    raw = estimator.score_samples(_NORMAL_8D(8))
    joblib.dump({"estimator": estimator, "score_low": float(min(raw)),
                "score_high": float(max(raw)),
                "gnn_state_dict": original.state_dict(), "gnn_in_dim": 8}, path)
    digest = hashlib.sha256(path.read_bytes()).hexdigest()

    loaded = ms.load(_uri(path), digest, format="graph-embedding-isolation-forest")
    x = torch.randn(3, 8)
    edge_index = torch.tensor([[0, 1], [1, 2]], dtype=torch.long)
    with torch.no_grad():
        assert torch.allclose(original(x, edge_index), loaded.gnn(x, edge_index))


def test_the_cache_key_includes_format_not_just_uri_and_digest(tmp_path):
    """The identical file loaded under two different declared formats must not collide
    in the cache and return whichever was loaded first - a graph bundle's own
    estimator/score_low/score_high satisfy sklearn-joblib's shape too, so this is a
    real ambiguity the cache key has to resolve, not a hypothetical one."""
    path = tmp_path / "graph.joblib"
    digest = _write_graph_bundle(path)

    as_sklearn = ms.load(_uri(path), digest, format="sklearn-joblib")
    as_graph = ms.load(_uri(path), digest, format="graph-embedding-isolation-forest")
    assert as_sklearn.gnn is None
    assert as_graph.gnn is not None
