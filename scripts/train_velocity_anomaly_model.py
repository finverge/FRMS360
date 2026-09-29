"""Train the AI/ML roadmap Phase 2 velocity/anomaly model (BRD OD-06/s19; HLD AD-14/s16).

Unsupervised on purpose - fitting a supervised model would need labelled fraud outcomes
this platform does not have for any tenant yet (that is exactly BR-806's backtesting
harness's job, once live traffic exists to backtest against). An isolation forest needs
no labels: it learns what a tenant's ordinary transaction shape looks like and scores
distance from it, which is the same "anomaly relative to the account's own baseline"
idea VEL-01 already applies to one dimension, generalised to several at once.

**What this produces is a first working implementation, not a validated one.** Trained
here against whatever traffic (synthetic or live) a tenant already has in
``analytics.fact_transaction`` - real inference, real governance gate, genuinely wired
into the detection worker - but its detection quality against real fraud outcomes is
unmeasured until BR-806's backtesting harness runs against a tenant with enough live
history to backtest, which is a separate, later exercise this script does not claim to
do.

Usage:
    python scripts/train_velocity_anomaly_model.py --tenant <tenant-id>
    python scripts/train_velocity_anomaly_model.py --tenant <tenant-id> --out var/models/velocity_anomaly_v1.joblib
"""
import argparse
import hashlib
import json
import os
import pathlib
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import text  # noqa: E402

from cp_common import SessionLocal  # noqa: E402
from cp_common.observations import CTR_PAISE, SMALL_CREDIT_PAISE  # noqa: E402
from services.analytics_service.app.detection.model_scoring import FEATURE_NAMES  # noqa: E402

DEFAULT_OUT = os.path.join("var", "models", "velocity_anomaly_v1.joblib")


def _feature_matrix(db, tenant_id: str) -> list[list[float]]:
    """One row per transaction, built from the same aggregates
    ``detection.features.load_context`` computes for live scoring - approximated over
    the account's *entire* history here rather than a rolling window, because this is an
    offline training pass over however much history exists, not a live batch. The
    formulas are identical to ``model_scoring.feature_vector``'s; only the window differs.
    """
    accounts = {r[0] for r in db.execute(text(
        "SELECT DISTINCT debtor_account FROM analytics.fact_transaction "
        "WHERE tenant_id = :t"), {"t": tenant_id}).all()}
    if not accounts:
        return []

    baseline = {r["a"]: float(r["mean_amt"] or 0) for r in db.execute(text(
        "SELECT debtor_account AS a, AVG(amount_paise) AS mean_amt "
        "FROM analytics.fact_transaction WHERE tenant_id = :t "
        "GROUP BY debtor_account"), {"t": tenant_id}).mappings()}

    # Same thresholds cp_common.observations uses for VEL-01/SME-01 - imported, not
    # retyped, so this training pass can never quietly drift from what live scoring
    # actually measures.
    flow = {r["a"]: r for r in db.execute(text(
        "SELECT debtor_account AS a, COALESCE(SUM(amount_paise), 0) AS out_paise, "
        "       COUNT(*) FILTER (WHERE amount_paise >= :ctr_lo AND amount_paise < :ctr) "
        "         AS sub_ctr "
        "FROM analytics.fact_transaction WHERE tenant_id = :t "
        "GROUP BY debtor_account"),
        {"t": tenant_id, "ctr": CTR_PAISE, "ctr_lo": int(CTR_PAISE * 0.9)}).mappings()}

    inflow = {r["a"]: r for r in db.execute(text(
        "SELECT creditor_account AS a, COALESCE(SUM(amount_paise), 0) AS in_paise, "
        "       COUNT(*) FILTER (WHERE amount_paise <= :small) AS small_credits "
        "FROM analytics.fact_transaction WHERE tenant_id = :t "
        "GROUP BY creditor_account"),
        {"t": tenant_id, "small": SMALL_CREDIT_PAISE}).mappings()}

    counterparties = {r["a"]: int(r["n"]) for r in db.execute(text(
        "SELECT a, COUNT(DISTINCT cp) AS n FROM ("
        "  SELECT debtor_account AS a, creditor_account AS cp "
        "    FROM analytics.fact_transaction WHERE tenant_id = :t "
        "  UNION ALL "
        "  SELECT creditor_account AS a, debtor_account AS cp "
        "    FROM analytics.fact_transaction WHERE tenant_id = :t) x "
        "GROUP BY a"), {"t": tenant_id}).mappings()}

    rows = db.execute(text(
        "SELECT debtor_account, amount_paise FROM analytics.fact_transaction "
        "WHERE tenant_id = :t"), {"t": tenant_id}).all()

    out = []
    for debtor, amount in rows:
        base = baseline.get(debtor, 0.0)
        value_vs_baseline = float(amount) / base if base > 0 else 0.0
        f = flow.get(debtor, {})
        i = inflow.get(debtor, {})
        out_p, in_p = float(f.get("out_paise") or 0), float(i.get("in_paise") or 0)
        outflow_ratio = out_p / in_p if in_p > 0 else 0.0
        out.append([
            value_vs_baseline,
            float(counterparties.get(debtor, 0)),
            outflow_ratio,
            float(i.get("small_credits") or 0),
            float(f.get("sub_ctr") or 0),
        ])
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--tenant", required=True,
                    help="tenant id (the analytics.fact_transaction.tenant_id UUID, "
                         "not the tenant's slug)")
    ap.add_argument("--out", default=DEFAULT_OUT,
                    help=f"where to write the joblib artifact (default: {DEFAULT_OUT})")
    ap.add_argument("--n-estimators", type=int, default=200)
    ap.add_argument("--contamination", default="auto",
                    help="expected anomaly fraction, or 'auto' (sklearn's default)")
    args = ap.parse_args()

    from sklearn.ensemble import IsolationForest  # deferred: heavy import, CLI-only tool

    db = SessionLocal()
    try:
        X = _feature_matrix(db, args.tenant)
    finally:
        db.close()

    if len(X) < 50:
        print(f"Only {len(X)} transactions found for tenant '{args.tenant}' - "
              "too few to fit a meaningful model. Run the synthetic data generator "
              "first, or point --tenant at one with real traffic.", file=sys.stderr)
        return 1

    contamination = args.contamination if args.contamination == "auto" else float(args.contamination)
    model = IsolationForest(n_estimators=args.n_estimators, contamination=contamination,
                            random_state=42)
    model.fit(X)

    # Calibration, not a guessed constant. score_samples is lower-is-more-abnormal with
    # no fixed range, and a fixed formula chosen without looking at this tenant's own
    # distribution put the bulk of ordinary traffic well above any sane threshold during
    # development. The 1st/99th percentile of *this fit's own* scores over its own
    # training set becomes the 0.0/1.0 ends of model_scoring.score()'s output - so a
    # threshold like "0.7" means the same thing (roughly: further into this tenant's own
    # tail than 70% of the calibration range) for every tenant, whatever their raw
    # score_samples numbers happen to look like.
    raw_scores = model.score_samples(X)
    score_low = float(sorted(raw_scores)[int(len(raw_scores) * 0.01)])
    score_high = float(sorted(raw_scores)[int(len(raw_scores) * 0.99)])

    import joblib

    os.makedirs(os.path.dirname(os.path.abspath(args.out)) or ".", exist_ok=True)
    joblib.dump({"estimator": model, "score_low": score_low, "score_high": score_high},
                args.out)

    with open(args.out, "rb") as f:
        digest = hashlib.sha256(f.read()).hexdigest()

    # pathlib's own as_uri(), not hand-concatenated - see model_scoring.py's
    # _read_bytes() docstring for why a naive "file://" + path silently drops the
    # Windows drive letter into the URI's netloc instead of its path.
    uri = pathlib.Path(args.out).resolve().as_uri()
    artifact = {"format": "sklearn-joblib", "uri": uri, "sha256": digest}

    print(f"Trained on {len(X)} transactions from '{args.tenant}', "
          f"features={list(FEATURE_NAMES)}")
    print(f"Wrote {args.out} ({os.path.getsize(args.out)} bytes)")
    print()
    print("model_artifact for this version's config body:")
    print(json.dumps(artifact, indent=2))
    print()
    print("This is a first working fit against available history, not a validated "
          "model - run BR-806's backtesting harness before proposing activation on a "
          "tenant with real, labelled outcomes to compare against.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
