"""AI Insights: a real local-LLM call (Ollama's OpenAI-compatible endpoint) over real
evidence data - see services/analytics_service/app/ai_insights.py.

The "happy path" tests need an actual model running at LLM_BASE_URL, which is true on
this dev machine but won't be true on CI or another engineer's box - they're skipped,
not failed, when the host isn't reachable, so this file stays honest about being an
integration test rather than a mock that would pass regardless of whether the real
integration still works.
"""
import requests
import pytest

from services.analytics_service.app import ai_insights


def _llm_reachable() -> bool:
    try:
        requests.get(ai_insights.LLM_BASE_URL.replace("/v1", "/api/tags"), timeout=2)
        return True
    except requests.RequestException:
        return False


needs_llm = pytest.mark.skipif(not _llm_reachable(), reason="no local LLM reachable at LLM_BASE_URL")


def _a_real_alert_id(analytics_client, tid, headers) -> str:
    row = analytics_client.get(
        f"/analytics/{tid}/drill/alert", headers=headers, params={"limit": 1},
    ).json()["rows"][0]
    return row["alert_id"]


@needs_llm
def test_ai_insights_generates_real_shape(analytics_client, token_for, tid):
    h = token_for("investigator")
    alert_id = _a_real_alert_id(analytics_client, tid, h)

    r = analytics_client.post(f"/analytics/{tid}/ai-insights/alert/{alert_id}", headers=h)
    assert r.status_code == 200, r.text
    body = r.json()

    assert body["entity"] == "alert"
    assert body["id"] == alert_id
    assert body["decision"] in ("ALLOW", "HOLD", "BLOCK")
    assert 0 <= body["risk_score"] <= 100
    assert 0 <= body["confidence"] <= 100
    assert isinstance(body["recommendations"], list) and body["recommendations"]
    assert len(body["narrative"]) > 10
    assert body["model"] == ai_insights.LLM_MODEL


@needs_llm
def test_ai_insights_unknown_record_is_404(analytics_client, token_for, tid):
    h = token_for("investigator")
    r = analytics_client.post(f"/analytics/{tid}/ai-insights/alert/ADOESNOTEXIST999", headers=h)
    assert r.status_code == 404, r.text


def test_ai_insights_unavailable_llm_is_503(analytics_client, token_for, tid, monkeypatch):
    """When the model host can't be reached, the API must say so plainly rather than
    fabricate a plausible-looking result."""
    monkeypatch.setattr(ai_insights, "LLM_BASE_URL", "http://127.0.0.1:1")
    h = token_for("investigator")
    alert_id = _a_real_alert_id(analytics_client, tid, h)

    r = analytics_client.post(f"/analytics/{tid}/ai-insights/alert/{alert_id}", headers=h)
    assert r.status_code == 503, r.text
    assert r.json()["error"]["code"] == "llm_unavailable"
