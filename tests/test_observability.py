"""Metrics and structured access logging.

The failures worth guarding against here are not "no metrics". They are metrics that take
the scrape down through label cardinality, metrics that leak the customer list, and a
green dashboard sitting over a pipeline that stopped hours ago.
"""
import json

import pytest

from cp_common import observability as obs


@pytest.fixture(autouse=True)
def _fresh_registry():
    """Each test gets its own counters; the module-level ones are process-global."""
    yield


# ---------------------------------------------------------------- exposition format
def test_counter_renders_valid_prometheus_text():
    c = obs.Counter("cp_test_thing", "A thing.", ("service",))
    c.inc(service="alpha")
    c.inc(2, service="alpha")
    out = "\n".join(c.render())
    assert "# TYPE cp_test_thing counter" in out
    assert 'cp_test_thing_total{service="alpha"} 3' in out


def test_histogram_buckets_are_cumulative_and_end_at_inf():
    """A non-cumulative histogram silently produces nonsense quantiles."""
    h = obs.Histogram("cp_test_latency", "Latency.", ("route",), buckets=(0.1, 1.0))
    for v in (0.05, 0.5, 5.0):
        h.observe(v, route="/x")
    out = "\n".join(h.render())
    assert 'cp_test_latency_bucket{route="/x",le="0.1"} 1' in out
    assert 'cp_test_latency_bucket{route="/x",le="1"} 2' in out
    assert 'cp_test_latency_bucket{route="/x",le="+Inf"} 3' in out
    assert 'cp_test_latency_count{route="/x"} 3' in out
    assert 'cp_test_latency_sum{route="/x"} 5.55' in out


def test_gauge_replaces_rather_than_accumulates():
    g = obs.Gauge("cp_test_depth", "Depth.", ("stage",))
    g.set(10, stage="pending")
    g.set(3, stage="pending")
    assert 'cp_test_depth{stage="pending"} 3' in "\n".join(g.render())


def test_a_concurrency_gauge_survives_overlapping_requests():
    """set(1)/set(0) reports an idle service while a second request is still running."""
    g = obs.Gauge("cp_test_inflight", "In flight.", ("service",))
    g.inc(service="a")          # request one starts
    g.inc(service="a")          # request two starts
    g.dec(service="a")          # request one finishes
    assert 'cp_test_inflight{service="a"} 1' in "\n".join(g.render())
    g.dec(service="a")
    assert 'cp_test_inflight{service="a"} 0' in "\n".join(g.render())


def test_label_values_are_escaped():
    """An unescaped quote in a label makes the whole scrape unparseable."""
    c = obs.Counter("cp_test_escape", "Escaping.", ("route",))
    c.inc(route='/a"b\\c')
    out = "\n".join(c.render())
    assert r'route="/a\"b\\c"' in out


def test_a_missing_label_is_refused_rather_than_defaulted():
    c = obs.Counter("cp_test_missing", "Missing.", ("service", "route"))
    with pytest.raises(ValueError):
        c.inc(service="alpha")


# ---------------------------------------------------------------- cardinality
def test_no_metric_carries_a_tenant_label():
    """The most tempting label in a multi-tenant platform and the most damaging.

    Five hundred tenants times fifty routes times five status classes is 125,000 series,
    and the scrape output becomes a list of the customers.
    """
    for m in obs.REGISTRY._metrics:
        assert "tenant" not in m.labelnames, (
            f"{m.name} carries a tenant label: {m.labelnames}")
        assert "tenant_id" not in m.labelnames


def test_request_metrics_use_the_route_template_not_the_path(analytics_client, token_for,
                                                             tid):
    """Otherwise every case id mints a new time series and eventually kills the scrape."""
    for _ in range(2):
        analytics_client.get(f"/analytics/{tid}/board", headers=token_for("risk_manager"))
    body = obs.REGISTRY.render()
    assert "/analytics/{tenant_id}/board" in body
    assert tid not in body, "a tenant id leaked into the metrics output"


def test_an_unmatched_path_is_bucketed_not_echoed(analytics_client, token_for, tid):
    """A scanner probing random URLs is otherwise an unbounded label source."""
    analytics_client.get("/analytics/nope/definitely-not-a-route-9f3a",
                         headers=token_for("analyst"))
    body = obs.REGISTRY.render()
    assert "definitely-not-a-route-9f3a" not in body


# ---------------------------------------------------------------- the endpoint
def test_metrics_endpoint_requires_the_internal_key(analytics_client):
    """Metrics from a multi-tenant platform describe the customer base."""
    from cp_common import settings

    denied = analytics_client.get("/metrics")
    assert denied.status_code == 403

    ok = analytics_client.get("/metrics",
                              headers={"x-internal-key": settings.internal_api_key})
    assert ok.status_code == 200
    assert "cp_http_requests_total" in ok.text
    assert ok.headers["content-type"].startswith("text/plain")


def test_build_info_is_exposed(analytics_client):
    from cp_common import settings

    body = analytics_client.get(
        "/metrics", headers={"x-internal-key": settings.internal_api_key}).text
    assert 'cp_build_info{service="analytics-service"' in body


# ---------------------------------------------------------------- request id
def test_every_response_carries_a_request_id(analytics_client, token_for, tid):
    r = analytics_client.get(f"/analytics/{tid}/board", headers=token_for("risk_manager"))
    assert r.headers.get("x-request-id")


def test_an_inbound_request_id_is_preserved(analytics_client, token_for, tid):
    """One user action must be traceable across every service it touches."""
    rid = "trace-me-0001"
    r = analytics_client.get(f"/analytics/{tid}/board",
                             headers={**token_for("risk_manager"), "x-request-id": rid})
    assert r.headers["x-request-id"] == rid


def test_the_access_log_line_is_json_with_the_request_id(analytics_client, token_for,
                                                         tid, caplog):
    with caplog.at_level("INFO", logger="cp.access"):
        analytics_client.get(f"/analytics/{tid}/board",
                             headers={**token_for("risk_manager"),
                                      "x-request-id": "log-me-0002"})
    lines = [r.message for r in caplog.records if r.name == "cp.access"]
    assert lines, "no access log line was emitted"
    entry = json.loads(lines[-1])
    assert entry["request_id"] == "log-me-0002"
    assert entry["route"] == "/analytics/{tenant_id}/board"
    assert entry["status"] == 200
    assert entry["duration_ms"] >= 0
    # Tenant belongs in the log, which is access-controlled - never in a metric label.
    assert entry["tenant_id"] == tid


def test_a_failing_request_is_still_measured(analytics_client, token_for):
    """A route that only counts successes hides exactly the thing you page on."""
    analytics_client.get("/analytics/00000000-0000-0000-0000-000000000000/board",
                         headers=token_for("analyst"))
    body = obs.REGISTRY.render()
    assert 'status="200"' in body
    assert any(f'status="{s}"' in body for s in ("400", "403", "404", "422", "500"))


# ---------------------------------------------------------------- freshness
def test_pipeline_freshness_reports_depth_and_age(ingestion_client):
    """A healthy p99 means nothing if nothing has moved for six hours."""
    r = ingestion_client.get("/internal/ingest/freshness")
    assert r.status_code == 200
    body = r.json()
    assert set(body) >= {"pending", "oldest_pending_seconds", "stale_claims"}
    assert body["pending"] >= 0

    from cp_common import settings

    scrape = ingestion_client.get(
        "/metrics", headers={"x-internal-key": settings.internal_api_key}).text
    assert 'cp_queue_depth{service="ingestion-service",stage="pending"}' in scrape
    assert 'cp_pipeline_lag_seconds{service="ingestion-service",stage="pending"}' in scrape
    # A row claimed by a worker that then died is not "pending" and would otherwise sit
    # invisible forever.
    assert 'stage="claimed_stale"' in scrape
