"""AI/ML roadmap Phase 2/3 - transaction velocity/anomaly and GNN fraud-ring scoring
(BRD OD-06/§19; HLD AD-14/AD-15/§16).

Runs in-process inside this worker, not a separate service - see AD-14 for why. The
model reads exactly the account context ``features.load_context`` already builds for the
rules engine; no second feature pipeline, no extra SQL. This module owns two things the
rules engine does not need: turning that context into a fixed numeric feature vector, and
loading + caching the trained artifact a tenant's ``model`` config version points at.

**What this is not.** A model trained here (see ``scripts/train_velocity_anomaly_model
.py``) is a first working implementation over the synthetic corpus, wired end-to-end
through the same governance gates BR-807/808 already enforce - it is not a claim of
validated accuracy against real fraud outcomes, which is a separate, ongoing exercise
once a tenant has live traffic to backtest against (BR-806).
"""
from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from urllib.parse import urlparse

from cp_common.observations import AccountContext

log = logging.getLogger("analytics.model_scoring")

#: Fixed order - the model is trained and scored against exactly this vector. Reuses
#: values the rules engine already computes (VEL-01, LAY-01, LAY-02, VEL-03, SME-01's own
#: observations), never a second definition of the same measurement.
FEATURE_NAMES = (
    "value_vs_baseline", "distinct_counterparties", "outflow_ratio",
    "small_credit_count", "sub_ctr_count",
)


def feature_vector(txn: dict, ctx: AccountContext) -> list[float]:
    """The tenant-agnostic numeric feature vector for one transaction's debtor side.

    Absent history reads as 0.0, the neutral value for a ratio with no denominator -
    the same convention VEL-01/LAY-01 use for a brand-new account, not a value the model
    would read as anomalous on its own.
    """
    amount = float(txn["amount_paise"])
    value_vs_baseline = amount / ctx.baseline_mean_paise if ctx.baseline_mean_paise > 0 else 0.0
    outflow_ratio = ctx.outflow_paise / ctx.inflow_paise if ctx.inflow_paise > 0 else 0.0
    return [
        value_vs_baseline,
        float(ctx.distinct_counterparties),
        outflow_ratio,
        float(ctx.small_credit_count),
        float(ctx.sub_ctr_count),
    ]


class ModelLoadError(RuntimeError):
    """The artifact could not be fetched, verified or deserialised. Callers treat this
    the same way a config-service outage is treated elsewhere: log it, report VEL-04
    unmeasurable, never crash the batch over a model that will not load."""


@dataclass
class LoadedModel:
    estimator: object          # a fitted sklearn.ensemble.IsolationForest
    # Calibration bounds computed from the *training* set's own score_samples
    # distribution (its 1st/99th percentile - see train_velocity_anomaly_model.py) and
    # saved inside the artifact, not guessed at here. IsolationForest's raw score is
    # lower-is-more-abnormal and has no fixed range; a hand-picked constant transform
    # would put the bulk of a tenant's ordinary traffic well above any sane threshold,
    # exactly the miscalibration a fixed formula produced during development.
    score_low: float
    score_high: float
    sha256: str
    uri: str
    # Set only for format="graph-embedding-isolation-forest" (AI/ML roadmap Phase 3,
    # AD-15) - a reconstructed GraphSAGE encoder producing the embeddings `estimator`
    # then scores. None for Phase 2's plain feature-vector models, which have no
    # encoder stage at all.
    gnn: object | None = None


_CACHE: dict[tuple[str, str], LoadedModel] = {}


def _read_bytes(uri: str) -> bytes:
    """Only ``file://`` is supported today. Object storage (s3://, azure blob) is named
    in the roadmap as where a real deployment's artifacts live - not built, because
    nothing in this environment has that infrastructure to point at yet, and a URI
    scheme this loader silently ignored would be worse than one it refuses."""
    parsed = urlparse(uri)
    if parsed.scheme != "file":
        raise ModelLoadError(
            f"unsupported artifact URI scheme '{parsed.scheme}' - only file:// is "
            "implemented; object-storage schemes are on the roadmap, not built")
    path = parsed.path
    # A correctly-formed Windows file:// URI (file:///D:/x, three slashes - what
    # pathlib's Path.as_uri() produces) parses with a leading slash before the drive
    # letter, which POSIX paths never have. A hand-typed two-slash form
    # (file://D:/x) instead parses the drive letter into netloc, where it would
    # otherwise be silently dropped - reattached here rather than trusting every
    # caller to construct the three-slash form correctly.
    if len(parsed.netloc) == 2 and parsed.netloc[1] == ":":
        path = f"/{parsed.netloc}{path}"
    if len(path) >= 3 and path[0] == "/" and path[2] == ":":
        path = path[1:]
    try:
        with open(path, "rb") as f:
            return f.read()
    except OSError as exc:
        raise ModelLoadError(f"could not read artifact at {uri}: {exc}") from exc


def load(uri: str, sha256: str, format: str = "sklearn-joblib") -> LoadedModel:  # noqa: A002
    """Fetch, verify, deserialise - cached by (uri, sha256, format) so a batch does not
    hit disk once per transaction. A cache key on the digest, not just the URI, means a
    replacement file at the same path is loaded fresh rather than silently reusing a
    stale in-memory model - the same reason ``case_documents`` never overwrites, only
    supersedes.

    ``format`` is one of ``model_artifact.SUPPORTED_FORMATS`` - already validated at
    proposal time, so this trusts it rather than re-deriving it, the same relationship
    every other consumer of a config body has to its own validator.
    """
    key = (uri, sha256, format)
    hit = _CACHE.get(key)
    if hit is not None:
        return hit

    raw = _read_bytes(uri)
    digest = hashlib.sha256(raw).hexdigest()
    if digest != sha256.lower():
        raise ModelLoadError(
            f"artifact at {uri} does not match its recorded sha256 - refusing to load "
            f"a file that was mutated or truncated after {sha256.lower()[:12]}... was "
            f"recorded (got {digest[:12]}...)")

    import io

    import joblib

    try:
        bundle = joblib.load(io.BytesIO(raw))
    except Exception as exc:  # noqa: BLE001 - any deserialisation failure is a load failure
        raise ModelLoadError(f"artifact at {uri} did not deserialise: {exc}") from exc

    try:
        estimator = bundle["estimator"]
        score_low = float(bundle["score_low"])
        score_high = float(bundle["score_high"])
    except (TypeError, KeyError, ValueError) as exc:
        raise ModelLoadError(
            f"artifact at {uri} deserialised but is not a recognised bundle "
            f"(expected a dict with estimator/score_low/score_high): {exc}") from exc
    if score_high <= score_low:
        raise ModelLoadError(
            f"artifact at {uri} has a degenerate calibration range "
            f"(score_low={score_low}, score_high={score_high}) - the training set's "
            "own scores had no spread to calibrate against.")

    gnn = None
    if format == "graph-embedding-isolation-forest":
        # AI/ML roadmap Phase 3 (AD-15). Same bundle, one more component: the encoder
        # that produces what `estimator` scores. Imported lazily so a deployment that
        # never activates a graph model never pays PyTorch's import cost.
        from .graph_model import RingScoreEncoder

        try:
            gnn = RingScoreEncoder(in_dim=int(bundle["gnn_in_dim"]))
            gnn.load_state_dict(bundle["gnn_state_dict"])
            gnn.eval()
        except (TypeError, KeyError, ValueError, RuntimeError) as exc:
            raise ModelLoadError(
                f"artifact at {uri} declares format={format!r} but its bundle has no "
                f"usable gnn_state_dict/gnn_in_dim: {exc}") from exc
    elif format != "sklearn-joblib":
        raise ModelLoadError(
            f"artifact at {uri} declares format={format!r}, which this loader does "
            "not know how to deserialise - see model_artifact.SUPPORTED_FORMATS")

    loaded = LoadedModel(estimator=estimator, score_low=score_low,
                        score_high=score_high, sha256=digest, uri=uri, gnn=gnn)
    _CACHE[key] = loaded
    return loaded


def invalidate_cache() -> None:
    """Tests and a future admin action need to force a reload; production never calls
    this on a hot path - the sha256-keyed cache already handles a genuine replacement."""
    _CACHE.clear()


def score(model: LoadedModel, features: list[float]) -> float:
    """0.0 (typical, at or below the training set's 1st-percentile score) to 1.0
    (highly anomalous, at or above its 99th-percentile). Linear rescaling against the
    model's own calibration bounds, clamped at both ends - a live transaction can fall
    outside the range the training set ever saw, in either direction, and clamping
    keeps that a boundary value rather than a score outside the catalogue's own 0-1
    band. This is a relative scale tuned to *this tenant's own history*, not a
    calibrated probability of fraud - the number a catalogue threshold gets tuned
    against, same as every other rule's observed value."""
    raw = float(model.estimator.score_samples([features])[0])
    normalized = (model.score_high - raw) / (model.score_high - model.score_low)
    return max(0.0, min(1.0, normalized))
