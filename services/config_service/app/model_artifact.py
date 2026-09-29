"""The trained artifact behind a ``model`` config version (AI/ML roadmap Phase 2/3, BRD
OD-06/§19; HLD AD-14/AD-15/§16).

``challenger.py`` validates that a candidate was *evaluated*; ``attestation.py``
validates that a committee *approved* it. Neither says what actually gets loaded and run
against live traffic - a model version could pass both gates and still have no artifact
behind it at all, which is a version that can be activated but never scores anything, and
nobody would notice until an inspection asked what code produced a given alert.

**Why a reference, never the binary itself.** The artifact is a trained model file
(scikit-learn IsolationForest et al, joblib-serialized, per AD-14; a GraphSAGE encoder
bundled with one, per AD-15; ``onnx`` is declared as a future format, not built) -
megabytes, and binary. It does not belong in a JSONB config-version row any more than an
uploaded case document does; ``uri`` points at object storage the same way
``case_documents`` does, and ``sha256`` is checked at load time so a mutated or truncated
file at that URI is refused rather than silently scoring traffic with something nobody
approved.
"""
from __future__ import annotations

REQUIRED = ("format", "uri", "sha256")

#: Serialization formats the scoring runtime (analytics-service's model_scoring.py)
#: knows how to load. ``onnx`` is named in the roadmap as a future option but nothing
#: loads it yet - declaring an artifact in that format is refused, not silently ignored.
#: ``graph-embedding-isolation-forest`` (AD-15) is one joblib bundle carrying both a
#: GraphSAGE encoder's state dict and the IsolationForest that scores its embeddings -
#: two models, one artifact, one version to evaluate and approve, not two.
SUPPORTED_FORMATS = ("sklearn-joblib", "graph-embedding-isolation-forest")


class ModelArtifactError(ValueError):
    """The model artifact reference is missing or unusable. Never resolved by a
    default - an unscoreable model version must fail loudly at proposal, not silently
    at the first batch that tries to load it."""


def requires_artifact(kind: str) -> bool:
    """Only the model kind - see the module docstring."""
    return (kind or "").strip().lower() == "model"


def validate(body: dict) -> dict:
    """Return the normalised artifact reference, or raise with what is wrong.

    Checked at proposal time, the same point ``challenger.validate`` and
    ``attestation.validate`` are - a model version with no loadable artifact should
    never reach ``pending_activation`` in the first place.
    """
    art = (body or {}).get("model_artifact")
    if not isinstance(art, dict) or not art:
        raise ModelArtifactError(
            "This model version has no trained artifact recorded. A model that cannot "
            "be loaded cannot be scored, whatever its evaluation says. Supply "
            f"model_artifact with {', '.join(REQUIRED)}.")

    missing = [f for f in REQUIRED if not str(art.get(f) or "").strip()]
    if missing:
        raise ModelArtifactError(
            f"The model artifact reference is incomplete: {', '.join(missing)} "
            "missing. The scoring runtime cannot load, verify or run it without all "
            "three.")

    fmt = str(art["format"]).strip().lower()
    if fmt not in SUPPORTED_FORMATS:
        raise ModelArtifactError(
            f"format '{fmt}' is not one this platform's scoring runtime loads. "
            f"Supported: {', '.join(SUPPORTED_FORMATS)}.")

    uri = str(art["uri"]).strip()
    sha256 = str(art["sha256"]).strip().lower()
    if len(sha256) != 64 or any(c not in "0123456789abcdef" for c in sha256):
        raise ModelArtifactError(
            f"sha256 '{art['sha256']}' is not a 64-character hex digest. Copy it "
            "exactly from the training run that produced this artifact - a truncated "
            "or malformed digest would refuse to load at scoring time anyway, just "
            "later and less clearly.")

    return {"format": fmt, "uri": uri, "sha256": sha256}
