"""Train the AI/ML roadmap Phase 3 GNN fraud-ring model (BRD OD-06/s19; HLD AD-15/s16).

Two models, one artifact: a GraphSAGE encoder (services/analytics_service/app/detection
/graph_model.py) trained unsupervised on link prediction over the tenant's own account
graph, and an IsolationForest fitted on the embeddings it produces - the same
anomaly-scoring approach Phase 2's velocity model uses, applied to a richer feature
space instead of a hand-picked one. Both are bundled into one joblib artifact so the
`model` config kind still records one evaluation and one committee approval per version,
not two.

**What this produces is a first working implementation, not a validated one** - same
caveat as scripts/train_velocity_anomaly_model.py, for the same reason: no tenant has
confirmed ring outcomes to validate against yet (BR-806's backtesting harness is the
separate, later exercise that would do that).

Usage:
    python scripts/train_graph_ring_score_model.py --tenant <tenant-id>
    python scripts/train_graph_ring_score_model.py --tenant <tenant-id> --out var/models/graph_ring_score_v1.joblib
"""
import argparse
import hashlib
import json
import os
import pathlib
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from cp_common import SessionLocal  # noqa: E402
from services.analytics_service.app.detection import graph_model as gm  # noqa: E402

DEFAULT_OUT = os.path.join("var", "models", "graph_ring_score_v1.joblib")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--tenant", required=True,
                    help="tenant id (the analytics.fact_transaction.tenant_id UUID, "
                         "not the tenant's slug)")
    ap.add_argument("--out", default=DEFAULT_OUT,
                    help=f"where to write the joblib artifact (default: {DEFAULT_OUT})")
    ap.add_argument("--window-days", type=int, default=90,
                    help="how much history to build the training graph from")
    ap.add_argument("--epochs", type=int, default=200)
    ap.add_argument("--n-estimators", type=int, default=200,
                    help="IsolationForest trees, fit on the encoder's embeddings")
    args = ap.parse_args()

    db = SessionLocal()
    try:
        graph = gm.build_tenant_graph(db, args.tenant, datetime.now(timezone.utc),
                                      window_days=args.window_days)
    finally:
        db.close()

    if len(graph.accounts) < 50:
        print(f"Only {len(graph.accounts)} accounts found for tenant '{args.tenant}' - "
              "too few to fit a meaningful graph. Run the synthetic data generator "
              "first, or point --tenant at one with real traffic.", file=sys.stderr)
        return 1

    print(f"Training graph: {len(graph.accounts)} accounts, "
          f"{graph.edge_index.shape[1] // 2} distinct edges")
    encoder = gm.train_encoder(graph, epochs=args.epochs)
    embeddings = gm.embed(encoder, graph)
    X = [embeddings[a] for a in graph.accounts]

    from sklearn.ensemble import IsolationForest  # deferred: heavy import, CLI-only tool

    estimator = IsolationForest(n_estimators=args.n_estimators, random_state=42)
    estimator.fit(X)

    # Same percentile calibration as Phase 2, against this fit's own scores over its
    # own training embeddings - see train_velocity_anomaly_model.py for why a fixed
    # constant transform is the wrong choice here.
    raw_scores = estimator.score_samples(X)
    score_low = float(sorted(raw_scores)[int(len(raw_scores) * 0.01)])
    score_high = float(sorted(raw_scores)[int(len(raw_scores) * 0.99)])

    import joblib

    bundle = {
        "estimator": estimator,
        "score_low": score_low,
        "score_high": score_high,
        "gnn_state_dict": encoder.state_dict(),
        "gnn_in_dim": len(gm.NODE_FEATURE_NAMES),
    }
    os.makedirs(os.path.dirname(os.path.abspath(args.out)) or ".", exist_ok=True)
    joblib.dump(bundle, args.out)

    with open(args.out, "rb") as f:
        digest = hashlib.sha256(f.read()).hexdigest()

    uri = pathlib.Path(args.out).resolve().as_uri()
    artifact = {"format": "graph-embedding-isolation-forest", "uri": uri, "sha256": digest}

    print(f"Trained on {len(graph.accounts)} accounts from '{args.tenant}', "
          f"node features={list(gm.NODE_FEATURE_NAMES)}, embedding_dim={gm.EMBEDDING_DIM}")
    print(f"Wrote {args.out} ({os.path.getsize(args.out)} bytes)")
    print()
    print("model_artifact for this version's config body:")
    print(json.dumps(artifact, indent=2))
    print()
    print("This is a first working fit against available history, not a validated "
          "model - run BR-806's backtesting harness before proposing activation on a "
          "tenant with real, confirmed ring outcomes to compare against.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
