"""PII masking, reveal gating and audit-of-view (DPDP)."""
import pytest

from services.analytics_service.app.privacy import PII_FIELDS, mask_value

MASK = "•"


def _rows(client, tid, headers, entity="transaction", **params):
    r = client.get(f"/analytics/{tid}/drill/{entity}", headers=headers,
                   params={"limit": 3, **params})
    assert r.status_code == 200, r.text
    return r.json()


def test_mask_value_keeps_a_correlatable_tail():
    masked = mask_value("debtor_account", "AC1747162981")
    assert masked.startswith("AC") and masked.endswith("2981") and MASK in masked
    assert "1747" not in masked


def test_ip_is_masked_to_two_octets():
    assert mask_value("ip_addr", "85.170.206.16") == f"85.170.{MASK}.{MASK}"


def test_rows_are_masked_by_default(analytics_client, token_for, tid):
    data = _rows(analytics_client, tid, token_for("investigator"))
    assert data["pii_masked"] is True
    for row in data["rows"]:
        for field in PII_FIELDS & set(row):
            assert MASK in str(row[field]), f"{field} left the server in the clear"


def test_dashboard_payloads_never_carry_clear_pii(analytics_client, token_for, tid):
    """Embedded dashboard rows must be masked too, not only the drill endpoint."""
    from cp_common.rbac import ROLES
    for dash in ROLES["tenant_admin"].dashboards:
        r = analytics_client.get(f"/analytics/{tid}/{dash}", headers=token_for("tenant_admin"))
        assert r.status_code == 200
        for row in r.json().get("rows", []):
            for field in PII_FIELDS & set(row):
                if row[field]:
                    assert MASK in str(row[field]), f"{dash} leaked {field}"


@pytest.mark.parametrize("params,expected", [
    ({"reveal": "true"}, 400),                              # no justification
    ({"reveal": "true", "justification": "short"}, 400),    # too short
])
def test_reveal_requires_a_justification(analytics_client, token_for, tid, params, expected):
    r = analytics_client.get(f"/analytics/{tid}/drill/transaction",
                             headers=token_for("investigator"), params={"limit": 1, **params})
    assert r.status_code == expected


def test_roles_without_capability_cannot_reveal(analytics_client, token_for, tid):
    for role in ("board", "data_scientist"):
        r = analytics_client.get(
            f"/analytics/{tid}/drill/case", headers=token_for(role),
            params={"limit": 1, "reveal": "true", "justification": "a valid looking reason"})
        assert r.status_code == 403, f"{role} was allowed to unmask"


def test_authorised_reveal_returns_clear_values(analytics_client, token_for, tid):
    data = _rows(analytics_client, tid, token_for("investigator"),
                 reveal="true", justification="Case C-1188 mule ring review")
    assert data["pii_masked"] is False
    assert any(MASK not in str(r["debtor_account"]) for r in data["rows"])


def test_reveal_and_evidence_views_are_audited(analytics_client, tenant_client, token_for, tid):
    from cp_common import SessionLocal, AuditLog
    from sqlalchemy import select

    analytics_client.get(f"/analytics/{tid}/drill/transaction", headers=token_for("investigator"),
                         params={"limit": 1, "reveal": "true",
                                 "justification": "audit trail verification"})
    alert_id = _rows(analytics_client, tid, token_for("investigator"),
                     entity="alert")["rows"][0]["alert_id"]
    analytics_client.get(f"/analytics/{tid}/evidence/alert/{alert_id}",
                         headers=token_for("investigator"))

    db = SessionLocal()
    try:
        actions = {a.action for a in db.scalars(
            select(AuditLog).where(AuditLog.tenant_id == tid))}
    finally:
        db.close()
    assert "data.reveal_pii" in actions, "unmasking was not audited"
    assert "data.view_evidence" in actions, "per-customer access was not audited"


def test_graph_pseudonyms_are_unique(analytics_client, token_for, tid):
    """Masking keeps only a prefix and last four digits, so two accounts can collide.
    In a graph that would merge two real accounts into one node and corrupt the
    structure, so masked node ids must stay distinct."""
    import collections
    g = analytics_client.get(f"/analytics/{tid}/graph",
                             headers=token_for("investigator")).json()
    ids = [n["id"] for n in g["nodes"]]
    dupes = [k for k, v in collections.Counter(ids).items() if v > 1]
    assert not dupes, f"masked node ids collided: {dupes[:3]}"
    known = set(ids)
    orphans = [e for e in g["edges"]
               if e["source"] not in known or e["target"] not in known]
    assert not orphans, "an edge references a node that is not in the node list"
