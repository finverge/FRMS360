"""Cross-cutting RBAC check for the three newest features (OFAC screening, AI Insights,
transaction geolocation): each should behave consistently with the *existing*
can_reveal_pii / module-access rules in cp_common.rbac.ROLES, not a rule of its own.

- OFAC screening needs only monitoring module access (every tenant role has it) - it
  must work the same for a board member as for an analyst, since it never touches a
  specific customer record.
- AI Insights works on masked data for anyone with monitoring access, and only escalates
  to unmasked data for roles that actually hold can_reveal_pii.
- Geolocation always needs the real IP, so it always needs reveal - which means it is
  only reachable at all for can_reveal_pii roles.
"""
import requests
import pytest

REVEAL_CAPABLE_ROLES = [
    "tenant_admin", "analyst", "investigator", "risk_manager",
    "principal_officer", "supervisor", "rbi_inspector",
]
REVEAL_INCAPABLE_ROLES = ["board", "cro", "data_scientist"]
ALL_TENANT_ROLES = REVEAL_CAPABLE_ROLES + REVEAL_INCAPABLE_ROLES
JUSTIFICATION = "role matrix verification"


def _llm_reachable() -> bool:
    from services.analytics_service.app import ai_insights
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


def _a_txn_with_public_ip(analytics_client, tid, headers) -> str:
    rows = analytics_client.get(
        f"/analytics/{tid}/drill/transaction", headers=headers,
        params={"limit": 200, "reveal": "true", "justification": JUSTIFICATION},
    ).json()["rows"]
    for row in rows:
        if row["ip_addr"] and not row["ip_addr"].startswith("10."):
            return row["txn_id"]
    raise AssertionError("no transaction with a non-private IP found in the seed window")


# ---------------- OFAC screening: identical for every role ----------------

@pytest.mark.parametrize("role", ALL_TENANT_ROLES)
def test_ofac_screen_works_for_every_tenant_role(analytics_client, token_for, tid, role):
    h = token_for(role)
    r = analytics_client.get(f"/analytics/{tid}/ofac-screen", headers=h, params={"name": "Cuba"})
    assert r.status_code == 200, f"{role}: {r.text}"


# ---------------- AI Insights: masked works for everyone, reveal is gated ----------------

@needs_llm
@pytest.mark.parametrize("role", ALL_TENANT_ROLES)
def test_ai_insights_masked_works_for_every_tenant_role(analytics_client, token_for, tid, role):
    h = token_for(role)
    alert_id = _a_real_alert_id(analytics_client, tid, token_for("investigator"))
    r = analytics_client.post(f"/analytics/{tid}/ai-insights/alert/{alert_id}", headers=h)
    assert r.status_code == 200, f"{role}: {r.text}"


@needs_llm
@pytest.mark.parametrize("role", REVEAL_CAPABLE_ROLES)
def test_ai_insights_with_reveal_for_capable_roles(analytics_client, token_for, tid, role):
    h = token_for(role)
    alert_id = _a_real_alert_id(analytics_client, tid, h)
    r = analytics_client.post(
        f"/analytics/{tid}/ai-insights/alert/{alert_id}", headers=h,
        params={"reveal": "true", "justification": JUSTIFICATION},
    )
    assert r.status_code == 200, f"{role}: {r.text}"


@pytest.mark.parametrize("role", REVEAL_INCAPABLE_ROLES)
def test_ai_insights_reveal_forbidden_for_incapable_roles(analytics_client, token_for, tid, role):
    h = token_for(role)
    alert_id = _a_real_alert_id(analytics_client, tid, token_for("investigator"))
    r = analytics_client.post(
        f"/analytics/{tid}/ai-insights/alert/{alert_id}", headers=h,
        params={"reveal": "true", "justification": JUSTIFICATION},
    )
    assert r.status_code == 403, f"{role}: {r.text}"
    assert r.json()["error"]["code"] == "pii_reveal_forbidden"


# ---------------- Geolocation: reveal is not optional, so it's gated the same way ----------------

@pytest.mark.parametrize("role", REVEAL_CAPABLE_ROLES)
def test_geolocate_with_reveal_for_capable_roles(analytics_client, token_for, tid, role):
    h = token_for(role)
    txn_id = _a_txn_with_public_ip(analytics_client, tid, token_for("investigator"))
    r = analytics_client.post(
        f"/analytics/{tid}/geolocate/{txn_id}", headers=h,
        params={"reveal": "true", "justification": JUSTIFICATION},
    )
    assert r.status_code == 200, f"{role}: {r.text}"
    assert r.json()["locatable"] is True


@pytest.mark.parametrize("role", REVEAL_INCAPABLE_ROLES)
def test_geolocate_reveal_forbidden_for_incapable_roles(analytics_client, token_for, tid, role):
    h = token_for(role)
    txn_id = _a_txn_with_public_ip(analytics_client, tid, token_for("investigator"))
    r = analytics_client.post(
        f"/analytics/{tid}/geolocate/{txn_id}", headers=h,
        params={"reveal": "true", "justification": JUSTIFICATION},
    )
    assert r.status_code == 403, f"{role}: {r.text}"
    assert r.json()["error"]["code"] == "pii_reveal_forbidden"


@pytest.mark.parametrize("role", REVEAL_INCAPABLE_ROLES)
def test_geolocate_without_reveal_is_reveal_required_not_a_crash(analytics_client, token_for, tid, role):
    """A role that could never reveal still gets the same honest 'reveal needed'
    message as a capable role that simply didn't ask - never a 500."""
    h = token_for(role)
    txn_id = _a_txn_with_public_ip(analytics_client, tid, token_for("investigator"))
    r = analytics_client.post(f"/analytics/{tid}/geolocate/{txn_id}", headers=h)
    assert r.status_code == 400, f"{role}: {r.text}"
    assert r.json()["error"]["code"] == "reveal_required"
