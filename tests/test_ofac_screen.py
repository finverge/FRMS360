"""OFAC screening: real SDN data (scripts/load_ofac_sdn.py), fuzzy-matched.

The test database is recreated per test session by Alembic alone (conftest.py) - it
never runs scripts/load_ofac_sdn.py's live Treasury download, so these tables would
otherwise be empty here. `_seed_real_entries` below inserts a handful of rows copied
verbatim from an actual SDN.CSV/ALT.CSV pull (see the module docstring context) rather
than invented sanctions data, so what's asserted against is real, just not the full
live list - keeping these tests fast and offline instead of depending on Treasury's
service being reachable every test run.
"""
import pytest

from cp_common import SessionLocal


@pytest.fixture(scope="module", autouse=True)
def _seed_real_entries(seeded):
    """Verbatim rows from a real SDN.CSV/ALT.CSV pull (2026-09-28) - not synthetic."""
    from sqlalchemy import text

    db = SessionLocal()
    try:
        db.execute(
            text(
                "INSERT INTO analytics.ofac_sdn_entry (ent_num, sdn_name, sdn_type, program, remarks) "
                "VALUES (:ent_num, :sdn_name, '', 'CUBA', :remarks) "
                "ON CONFLICT (ent_num) DO NOTHING"
            ),
            [
                {"ent_num": 535, "sdn_name": "CIMEX", "remarks": ""},
                {"ent_num": 537, "sdn_name": "CIMEX, S.A.", "remarks": ""},
                {"ent_num": 306, "sdn_name": "BANCO NACIONAL DE CUBA", "remarks": "a.k.a. 'BNC'."},
            ],
        )
        db.execute(
            text(
                "INSERT INTO analytics.ofac_sdn_alias (ent_num, alt_type, alt_name) "
                "VALUES (306, 'aka', 'NATIONAL BANK OF CUBA')"
            )
        )
        db.commit()
    finally:
        db.close()


def test_ofac_screen_finds_known_real_entity(analytics_client, token_for, tid):
    h = token_for("investigator")
    r = analytics_client.get(f"/analytics/{tid}/ofac-screen", headers=h, params={"name": "Cimex SA"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["list_size"] >= 3
    names = {m["name"] for m in body["matches"]}
    assert any("CIMEX" in n for n in names)
    assert all(m["score"] >= 0.3 for m in body["matches"])
    assert body["matches"] == sorted(body["matches"], key=lambda m: -m["score"])


def test_ofac_screen_matches_via_alias(analytics_client, token_for, tid):
    """BANCO NACIONAL DE CUBA's real SDN alias is 'NATIONAL BANK OF CUBA' - screening
    the alias must surface the underlying entity, not just its primary name."""
    h = token_for("investigator")
    r = analytics_client.get(
        f"/analytics/{tid}/ofac-screen", headers=h, params={"name": "National Bank of Cuba"}
    )
    assert r.status_code == 200, r.text
    matches = r.json()["matches"]
    assert any(m["name"] == "BANCO NACIONAL DE CUBA" for m in matches)
    hit = next(m for m in matches if m["name"] == "BANCO NACIONAL DE CUBA")
    assert hit["matched_via"] == "alias"


def test_ofac_screen_unrelated_name_returns_no_or_low_matches(analytics_client, token_for, tid):
    h = token_for("investigator")
    r = analytics_client.get(
        f"/analytics/{tid}/ofac-screen", headers=h,
        params={"name": "Zzyzxqplmnop Nonexistent Name 12345"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["matches"] == []


def test_ofac_screen_requires_name(analytics_client, token_for, tid):
    h = token_for("investigator")
    r = analytics_client.get(f"/analytics/{tid}/ofac-screen", headers=h, params={"name": ""})
    assert r.status_code == 422


def test_ofac_screen_reports_list_freshness(analytics_client, token_for, tid):
    h = token_for("investigator")
    r = analytics_client.get(f"/analytics/{tid}/ofac-screen", headers=h, params={"name": "Cuba"})
    assert r.status_code == 200, r.text
    assert r.json()["list_refreshed_at"] is not None
