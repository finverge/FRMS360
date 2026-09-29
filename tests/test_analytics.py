"""Dashboards, the metric registry, filters and drill-down."""
import collections

import pytest

from cp_common.rbac import DASHBOARDS
from services.analytics_service.app.metrics import REGISTRY

ADMIN = "tenant_admin"   # sees every tenant-scoped dashboard


def _dash(client, tid, headers, name, **params):
    r = client.get(f"/analytics/{tid}/{name}", headers=headers, params=params)
    assert r.status_code == 200, f"{name}: {r.text[:200]}"
    return r.json()


@pytest.mark.parametrize("name", [d for d in DASHBOARDS if d != "tenant_health"])
def test_dashboard_returns_content(analytics_client, token_for, tid, name):
    d = _dash(analytics_client, tid, token_for(ADMIN), name)
    assert d["metrics"], f"{name} returned no metrics"
    assert d["persona"]


def test_metrics_are_identical_across_dashboards(analytics_client, token_for, tid):
    """The registry guarantee: one definition per metric, so every dashboard agrees.
    Volatile (NOW()-relative) metrics are excluded - they are point-in-time by design."""
    seen = collections.defaultdict(dict)
    volatile = set()
    for name in DASHBOARDS:
        if name == "tenant_health":
            continue
        for metric, m in _dash(analytics_client, tid, token_for(ADMIN), name)["metrics"].items():
            seen[metric][name] = m["value"]
            if m.get("volatile"):
                volatile.add(metric)
    shared = {k: v for k, v in seen.items() if len(v) > 1 and k not in volatile}
    assert shared, "no metric appears on more than one dashboard - test is not proving anything"
    for metric, by_dash in shared.items():
        assert len(set(by_dash.values())) == 1, f"{metric} disagrees: {by_dash}"


def test_breakdowns_partition_exactly(analytics_client, token_for, tid):
    d = _dash(analytics_client, tid, token_for(ADMIN), "analyst")
    total = d["metrics"]["alert_count"]["value"]
    for key in ("by_family", "by_severity", "by_disposition", "by_rail"):
        assert sum(r["value"] for r in d["breakdowns"][key]) == total, f"{key} loses rows"


def test_registry_has_no_orphan_definitions():
    for name, m in REGISTRY.items():
        assert m.base in ("case", "alert", "transaction"), f"{name} has base {m.base}"
        assert "{b}" in m.expr or "COUNT(*)" in m.expr, f"{name} does not alias its columns"


@pytest.mark.parametrize("params,metric,dash", [
    ({"rails": "UPI"}, "alert_count", "analyst"),
    ({"families": "LAY"}, "alert_count", "analyst"),
    ({"severities": "critical"}, "alert_count", "analyst"),
    ({"rfa_only": "true"}, "case_count", "board"),
    ({"states": "fraud_declared"}, "case_count", "board"),
    ({"dispositions": "true_positive"}, "alert_count", "analyst"),
    ({"products": "loan"}, "transaction_count", "realtime"),
    ({"segments": "corporate"}, "transaction_count", "realtime"),
    ({"fmr_status": "filed"}, "case_count", "board"),
], ids=lambda p: str(p) if isinstance(p, dict) else p)
def test_filters_narrow_results(analytics_client, token_for, tid, params, metric, dash):
    h = token_for(ADMIN)
    base = _dash(analytics_client, tid, h, dash)["metrics"][metric]["value"]
    got = _dash(analytics_client, tid, h, dash, **params)["metrics"][metric]["value"]
    assert 0 <= got < base, f"{params} did not narrow {metric} ({got} vs {base})"


def test_timeseries_sums_to_the_headline(analytics_client, token_for, tid):
    """A trend line must be the same definition as the number above it."""
    h = token_for(ADMIN)
    for metric, dash in (("alert_count", "analyst"), ("case_count", "board"),
                         ("fraud_value_total", "board")):
        head = _dash(analytics_client, tid, h, dash)["metrics"][metric]["value"]
        r = analytics_client.get(f"/analytics/{tid}/timeseries", headers=h,
                                 params={"metric": metric, "bucket": "week"})
        assert r.status_code == 200, r.text
        assert sum(x["value"] for x in r.json()["rows"]) == head, f"{metric} trend != headline"


def test_timeseries_rejects_a_bad_bucket(analytics_client, token_for, tid):
    r = analytics_client.get(f"/analytics/{tid}/timeseries", headers=token_for(ADMIN),
                             params={"metric": "alert_count", "bucket": "fortnight"})
    assert r.status_code == 400


@pytest.mark.parametrize("entity", ["alert", "case", "transaction"])
def test_drill_and_evidence(analytics_client, token_for, tid, entity):
    h = token_for("investigator")
    rows = analytics_client.get(f"/analytics/{tid}/drill/{entity}", headers=h,
                                params={"limit": 2}).json()["rows"]
    assert rows, f"no {entity} rows"
    ident = rows[0][{"alert": "alert_id", "case": "case_id", "transaction": "txn_id"}[entity]]
    ev = analytics_client.get(f"/analytics/{tid}/evidence/{entity}/{ident}", headers=h).json()
    assert "error" not in ev
    if entity == "case":
        assert ev["value_check"]["reconciled"], "case value does not tie to its transactions"


def test_alert_evidence_pins_the_config_version(analytics_client, token_for, tid):
    """A case reopened later must be explained with the rules that actually ran."""
    h = token_for("investigator")
    alert = analytics_client.get(f"/analytics/{tid}/drill/alert", headers=h,
                                 params={"limit": 1}).json()["rows"][0]
    assert alert["config_version"], "no config version pinned on the alert"


def test_account360_pins_to_one_account(analytics_client, token_for, tid):
    h = token_for("investigator")
    row = analytics_client.get(f"/analytics/{tid}/drill/transaction", headers=h,
                               params={"limit": 1, "reveal": "true",
                                       "justification": "test fixture setup"}).json()["rows"][0]
    everything = _dash(analytics_client, tid, h, "account360")["metrics"]["transaction_count"]["value"]
    scoped = _dash(analytics_client, tid, h, "account360",
                   account=row["debtor_account"])["metrics"]["transaction_count"]["value"]
    assert 0 < scoped < everything


def test_graph_returns_a_real_structure(analytics_client, token_for, tid):
    """The injected mule rings must show up as an actual graph, not isolated pairs."""
    r = analytics_client.get(f"/analytics/{tid}/graph", headers=token_for("investigator"))
    assert r.status_code == 200, r.text
    g = r.json()
    assert g["nodes"] and g["edges"], "no network returned"
    assert g["pii_masked"] is True, "account ids left the server unmasked"
    # A fan-in/fan-out hub is the whole point: some node must have many counterparties.
    assert max(n["degree"] for n in g["nodes"]) >= 5, "no hub - the data has no ring in it"
    assert any(n["role"] == "hub" for n in g["nodes"]), "no node classified as a hub"


def test_graph_scoped_to_a_case_is_smaller(analytics_client, token_for, tid):
    h = token_for("investigator")
    whole = analytics_client.get(f"/analytics/{tid}/graph", headers=h).json()
    case_id = analytics_client.get(f"/analytics/{tid}/drill/case", headers=h,
                                   params={"limit": 1}).json()["rows"][0]["case_id"]
    scoped = analytics_client.get(f"/analytics/{tid}/graph", headers=h,
                                  params={"case_id": case_id}).json()
    assert scoped["scope"]["case_id"] == case_id
    assert len(scoped["nodes"]) <= len(whole["nodes"])


def test_graph_respects_pii_capability(analytics_client, token_for, tid):
    r = analytics_client.get(f"/analytics/{tid}/graph", headers=token_for("board"),
                             params={"reveal": "true", "justification": "board wants detail"})
    assert r.status_code == 403


def test_alert_rows_carry_the_evaluation_trace(analytics_client, token_for, tid):
    """A score alone cannot answer 'why'; the matched band and observation must travel."""
    rows = analytics_client.get(f"/analytics/{tid}/drill/alert", headers=token_for("analyst"),
                                params={"limit": 5}).json()["rows"]
    traced = [r for r in rows if r.get("matched_reason")]
    assert traced, "no alert carried a rule-evaluation trace"
    a = traced[0]
    assert a["sub_rule_ref"].startswith("."), "no sub-rule band recorded"
    assert a["observed_value"] is not None and a["threshold_value"] is not None
    assert a["config_version"], "trace is useless without the config version it ran under"


# ---------------- control plane -> detection bridge ----------------
def test_rule_catalogue_and_analytics_share_one_vocabulary(analytics_client, token_for, tid):
    """The control plane's rule ids must be the ones detection actually emits.

    They used to differ (config stored '018@1.0.0', alerts carried 'LAY-02'), which meant
    retuning a threshold in the console changed nothing.
    """
    r = analytics_client.get(f"/analytics/{tid}/dormant-rules", headers=token_for("analyst"))
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["available"], "rule catalogue could not be read from config-service"
    assert d["configured"] > 0
    assert not d["uncatalogued"], \
        f"detection emitted rules absent from the catalogue: {d['uncatalogued']}"


def test_dormant_register_is_filter_aware(analytics_client, token_for, tid):
    """Narrowing the window must surface indicators that go silent in it."""
    h = token_for("analyst")
    everything = analytics_client.get(f"/analytics/{tid}/dormant-rules", headers=h).json()
    narrow = analytics_client.get(f"/analytics/{tid}/dormant-rules", headers=h,
                                  params={"rails": "RTGS"}).json()
    assert narrow["coverage"] <= everything["coverage"]
    assert len(narrow["dormant"]) >= len(everything["dormant"])


def test_qualitative_indicators_are_reported_separately(analytics_client, token_for, tid):
    """QUAL rules are fed from CBS events, not the payment stream, so silence there is
    expected and must not be counted as a coverage failure."""
    d = analytics_client.get(f"/analytics/{tid}/dormant-rules",
                             headers=token_for("analyst")).json()
    assert d["configured"] > d["configured_quantitative"], "no qualitative rules configured"
    assert "dormant_qualitative" in d
    quant_ids = {x["rule_id"] for x in d["dormant"]}
    assert not any(r.startswith("QUAL") for r in quant_ids), \
        "a qualitative indicator was counted against quantitative coverage"


def test_ews_dashboard_carries_the_dormant_block(analytics_client, token_for, tid):
    d = analytics_client.get(f"/analytics/{tid}/ews", headers=token_for(ADMIN)).json()
    assert d.get("dormant") is not None, "EWS dashboard lost its coverage block"


def test_catalogue_unavailable_degrades_to_unknown(monkeypatch, analytics_client, token_for, tid):
    """If config-service is unreachable we must report 'unknown', not 'everything is
    dormant' - the latter is a false compliance alarm."""
    from services.analytics_service.app import rules as bridge
    bridge.invalidate()
    monkeypatch.setattr(bridge, "active_rules", lambda tenant_id, force=False: {})
    out = bridge.dormant_report(tid, {"LAY-01"})
    assert out["available"] is False
    assert out["dormant"] == [] and out["coverage"] is None


def test_coverage_numerator_matches_its_denominator(analytics_client, token_for, tid):
    """Reported as 'X of Y quantitative indicators fired', so X must be counted over the
    quantitative population too. Mixing them produced '28 of 25'."""
    d = analytics_client.get(f"/analytics/{tid}/dormant-rules",
                             headers=token_for("analyst")).json()
    assert d["fired_quantitative"] <= d["configured_quantitative"]
    assert d["fired_quantitative"] + len(d["dormant"]) == d["configured_quantitative"]
