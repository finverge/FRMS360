"""Model governance: inventory and override log (FREE-AI).

FREE-AI expects a regulated entity to govern models across their lifecycle - approval,
validation, change control, and human accountability for outcomes - not merely to measure
accuracy. Two things were missing:

**Inventory.** What is actually in force, who owns it, when it was last validated. The
platform already knows which config versions produced scores, so the inventory is derived
from what ran rather than typed into a spreadsheet that drifts from reality. A version
observed in production but never approved is the finding worth surfacing.

**Override log.** Where a human disagreed with the model. A high-scoring alert closed as a
false positive is an override; so is a low-scoring one escalated. Neither is wrong - that
is what human accountability means - but a rising override rate says the model and the
people using it have diverged, and that is invisible in precision alone.
"""
from sqlalchemy import text

# A score at or above this was a strong model signal; dismissing it is a real disagreement.
STRONG_SIGNAL = 300.0
# At or below this the model was not concerned; escalating it is the opposite disagreement.
WEAK_SIGNAL = 150.0


def inventory(engine, tenant_id: str, approved_versions: set[str]) -> dict:
    """Config versions observed in production, with usage and outcome quality.

    ``approved_versions`` comes from the control plane. Anything scoring traffic that is
    not in that set is an unapproved model in production.
    """
    rows = engine.all_sql(
        """
        SELECT config_version,
               COUNT(*)                                            AS alerts,
               MIN(ts)                                             AS first_seen,
               MAX(ts)                                             AS last_seen,
               COUNT(*) FILTER (WHERE disposition = 'true_positive')  AS tp,
               COUNT(*) FILTER (WHERE disposition = 'false_positive') AS fp,
               COUNT(DISTINCT rule_id)                             AS rules
        FROM fact_alert
        WHERE tenant_id = :tenant_id
        GROUP BY config_version
        ORDER BY 2 DESC
        """,
        {"tenant_id": tenant_id})

    models = []
    for version, alerts, first_seen, last_seen, tp, fp, rules in rows:
        judged = (tp or 0) + (fp or 0)
        models.append({
            "config_version": version,
            "approved": version in approved_versions,
            "alerts": int(alerts),
            "rules_exercised": int(rules),
            "first_seen": first_seen.isoformat() if first_seen else None,
            "last_seen": last_seen.isoformat() if last_seen else None,
            "precision": round((tp or 0) / judged, 4) if judged else None,
            "dispositioned": judged,
        })
    unapproved = [m["config_version"] for m in models if not m["approved"]]
    return {
        "models": models,
        "in_production": len(models),
        "approved_in_control_plane": sorted(approved_versions),
        "unapproved_in_production": unapproved,
        "governance_gap": bool(unapproved),
    }


def overrides(engine, tenant_id: str, limit: int = 200) -> dict:
    """Alerts where the analyst's disposition contradicted the model's signal."""
    rows = engine.all_sql(
        """
        SELECT alert_id, rule_id, rule_family, score, disposition, analyst,
               config_version, disposition_ts
        FROM fact_alert
        WHERE tenant_id = :tenant_id
          AND disposition IN ('true_positive', 'false_positive')
          AND ((score >= :strong AND disposition = 'false_positive')
            OR (score <= :weak   AND disposition = 'true_positive'))
        ORDER BY disposition_ts DESC NULLS LAST
        LIMIT :lim
        """,
        {"tenant_id": tenant_id, "strong": STRONG_SIGNAL, "weak": WEAK_SIGNAL,
         "lim": limit})

    items = [{
        "alert_id": a, "rule_id": r, "family": fam, "score": float(sc),
        "disposition": d, "analyst": an or "unassigned", "config_version": cv,
        "kind": "dismissed_strong_signal" if d == "false_positive" else "escalated_weak_signal",
        "at": ts.isoformat() if ts else None,
    } for a, r, fam, sc, d, an, cv, ts in rows]

    judged = engine.scalar_sql(
        "SELECT COUNT(*) FROM fact_alert WHERE tenant_id = :tenant_id "
        "AND disposition IN ('true_positive','false_positive')",
        {"tenant_id": tenant_id}) or 0

    by_analyst: dict[str, int] = {}
    by_rule: dict[str, int] = {}
    for it in items:
        by_analyst[it["analyst"]] = by_analyst.get(it["analyst"], 0) + 1
        by_rule[it["rule_id"]] = by_rule.get(it["rule_id"], 0) + 1

    return {
        "thresholds": {"strong_signal": STRONG_SIGNAL, "weak_signal": WEAK_SIGNAL},
        "dispositioned": int(judged),
        "overrides": len(items),
        "override_rate": round(len(items) / judged, 4) if judged else None,
        "dismissed_strong": sum(1 for i in items if i["kind"] == "dismissed_strong_signal"),
        "escalated_weak": sum(1 for i in items if i["kind"] == "escalated_weak_signal"),
        "by_analyst": sorted(({"analyst": k, "overrides": v} for k, v in by_analyst.items()),
                             key=lambda x: -x["overrides"])[:10],
        "by_rule": sorted(({"rule_id": k, "overrides": v} for k, v in by_rule.items()),
                          key=lambda x: -x["overrides"])[:10],
        "recent": items[:50],
    }
