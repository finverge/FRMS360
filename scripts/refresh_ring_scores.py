"""Refresh AI/ML roadmap Phase 3's fraud-ring scores (BRD OD-06/§19; HLD AD-15/§16).

    python scripts/refresh_ring_scores.py                  # every tenant with an
                                                            # active graph-ring-score model
    python scripts/refresh_ring_scores.py --tenant <id>

Intended to run on a schedule (cron / Task Scheduler), the same shape as
run_retention.py and run_subscriptions.py - not a daemon, not a new service. A GraphSAGE
forward pass over a tenant's whole account graph does not fit the detection worker's
per-batch budget the way Phase 2's five-feature IsolationForest call does (HLD AD-15), so
this script does the expensive part on its own cadence and writes the cheap-to-read
result (analytics.account_ring_score) that engine.py's LAY-05 lookup just selects from.

A tenant with no active ``graph-ring-score`` model is skipped, not scored on a fallback -
there is no safe fallback GNN the way there is a safe fallback policy, same reasoning
Phase 2's model-scoring step already has.
"""
import argparse
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import text  # noqa: E402

from cp_common import SessionLocal, record_audit  # noqa: E402
from services.analytics_service.app.detection import features  # noqa: E402
from services.analytics_service.app.detection import graph_model as gm  # noqa: E402
from services.analytics_service.app.detection import model_scoring  # noqa: E402
from services.analytics_service.app.rules import active_model  # noqa: E402

ACTOR = "system:ring-score-refresh"
#: The named model LAY-05 is backed by - read from the same registry engine.py's own
#: unmeasurable-reporting reads, not retyped here as a literal that could drift from it.
MODEL_NAME = features.NEEDS_MODEL["LAY-05"]


def _tenants(db, only: str | None) -> list[str]:
    if only:
        return [only]
    return [r[0] for r in db.execute(text(
        "SELECT id FROM tenant.tenants ORDER BY display_name")).all()]


def refresh_one(db, tenant_id: str, *, window_days: int = 90) -> dict:
    """Returns a small report dict; never raises for an unavailable or unloadable
    model - the same "skip, don't crash" contract engine.py's own model-scoring step
    has, so a bad artifact on one tenant does not stop the run for every other one."""
    info = active_model(tenant_id, MODEL_NAME, force=True)
    if not info.get("available"):
        return {"tenant_id": tenant_id, "status": "skipped",
                "reason": f"no active '{MODEL_NAME}' model version"}

    artifact = (info.get("body") or {}).get("model_artifact") or {}
    try:
        loaded = model_scoring.load(artifact["uri"], artifact["sha256"],
                                    format=artifact.get("format", "sklearn-joblib"))
    except (KeyError, model_scoring.ModelLoadError) as exc:
        return {"tenant_id": tenant_id, "status": "skipped",
                "reason": f"model artifact could not be loaded: {exc}"}
    if loaded.gnn is None:
        return {"tenant_id": tenant_id, "status": "skipped",
                "reason": f"active '{MODEL_NAME}' version is not a graph model "
                          f"(format={artifact.get('format')!r})"}

    graph = gm.build_tenant_graph(db, tenant_id, datetime.now(timezone.utc),
                                  window_days=window_days)
    if not graph.accounts:
        return {"tenant_id": tenant_id, "status": "skipped",
                "reason": "no transaction graph in the window"}

    embeddings = gm.embed(loaded.gnn, graph)
    now = datetime.now(timezone.utc)
    rows = [{"tenant_id": tenant_id, "account": a,
            "score": model_scoring.score(loaded, embeddings[a]),
            "model_version": info["version"], "computed_at": now}
           for a in graph.accounts]

    db.execute(text(
        "INSERT INTO analytics.account_ring_score "
        "(tenant_id, account, score, model_version, computed_at) "
        "VALUES (:tenant_id, :account, :score, :model_version, :computed_at) "
        "ON CONFLICT (tenant_id, account) DO UPDATE SET "
        "  score = EXCLUDED.score, model_version = EXCLUDED.model_version, "
        "  computed_at = EXCLUDED.computed_at"), rows)
    db.commit()

    record_audit(
        service="analytics-service", action="model.ring_score_refresh", actor=ACTOR,
        actor_role="system", tenant_id=tenant_id, target_type="model",
        target_id=MODEL_NAME, status="success",
        detail={"accounts_scored": len(rows), "model_version": info["version"]})

    return {"tenant_id": tenant_id, "status": "refreshed", "accounts_scored": len(rows),
           "model_version": info["version"]}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--tenant", default=None)
    ap.add_argument("--window-days", type=int, default=90)
    args = ap.parse_args()

    db = SessionLocal()
    try:
        for tid in _tenants(db, args.tenant):
            report = refresh_one(db, tid, window_days=args.window_days)
            if report["status"] == "refreshed":
                print(f"{tid}: refreshed {report['accounts_scored']} accounts "
                     f"(model v{report['model_version']})")
            else:
                print(f"{tid}: skipped - {report['reason']}")
    finally:
        db.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
