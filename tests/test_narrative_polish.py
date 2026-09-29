"""LLM rephrasing of the deterministic filing narrative (modus_operandi,
suspicion_grounds) — off by default, and its one real job is proven here:
a rewrite that adds a fact not already in the source is rejected, not
silently accepted. See services/analytics_service/app/filings/
narrative_polish.py for why this exists and what it deliberately does
not do.
"""
import pytest
from sqlalchemy import select, text

from cp_common import settings
from cp_common.db import SessionLocal
from services.analytics_service.app.filings import narrative_polish
from services.analytics_service.app.filings_model import RegulatoryFiling
from tests.test_filings import case  # noqa: F401 - same case-seeding fixture, reused as-is


# --------------------------------------------------------- pure logic (no LLM, no DB)

def test_extract_facts_finds_numbers_and_rule_ids():
    facts = narrative_polish._extract_facts(
        "3 transaction(s) totalling INR 450,000.00 triggered EWS-014 and LAY-02.")
    assert "450000.00" in facts
    assert "3" in facts
    assert "EWS-014" in facts
    assert "LAY-02" in facts


def test_extract_facts_ignores_plain_words():
    facts = narrative_polish._extract_facts("Automated monitoring flagged this account.")
    assert facts == set()


def test_polish_field_accepts_a_faithful_rewrite(monkeypatch):
    original = "3 transaction(s) totalling INR 450000.00 triggered 2 indicator(s)."
    monkeypatch.setattr(
        narrative_polish, "_call_llm",
        lambda system_prompt, user_input: "Our monitoring detected three transactions "
                                           "totalling INR 450000.00, triggering two indicators.")

    result = narrative_polish.polish_field("modus_operandi", original)
    assert result.verified is True
    assert result.rejection_reason is None
    assert "450000.00" in result.polished


def test_polish_field_rejects_a_rewrite_that_invents_a_number(monkeypatch):
    """The core safety property: a plausible-sounding rewrite that adds a fact not in
    the source must never be handed back as if it were safe to use."""
    original = "3 transaction(s) totalling INR 450000.00 triggered 2 indicator(s)."
    monkeypatch.setattr(
        narrative_polish, "_call_llm",
        lambda system_prompt, user_input: "Our monitoring detected three transactions "
                                           "totalling INR 999999.00, triggering two indicators.")

    result = narrative_polish.polish_field("modus_operandi", original)
    assert result.verified is False
    assert result.polished == original  # never the unverified rewrite
    assert "999999.00" in result.rejection_reason


def test_polish_field_rejects_a_rewrite_that_invents_a_rule_id(monkeypatch):
    original = "Flagged by EWS-014 for velocity anomaly."
    monkeypatch.setattr(
        narrative_polish, "_call_llm",
        lambda system_prompt, user_input: "Flagged by EWS-014 and LAY-09 for a velocity anomaly.")

    result = narrative_polish.polish_field("suspicion_grounds", original)
    assert result.verified is False
    assert "LAY-09" in result.rejection_reason


def test_polish_field_allows_facts_present_only_in_the_evidence_context(monkeypatch):
    """A number that's genuinely in the structured evidence (not the deterministic
    prose itself) is still a real fact, not an invention — the rewrite is allowed to
    surface it, since it did not add anything the platform doesn't already know."""
    original = "Multiple indicators triggered on this account."
    evidence = "EWS-014 observed 46 distinct_counterparties vs threshold 20"
    monkeypatch.setattr(
        narrative_polish, "_call_llm",
        lambda system_prompt, user_input: "This account triggered EWS-014, "
                                           "with 46 counterparties observed against a threshold of 20.")

    result = narrative_polish.polish_field("modus_operandi", original, evidence_context=evidence)
    assert result.verified is True


def test_polish_narrative_fields_skips_absent_fields():
    """An FMR payload has no suspicion_grounds at all — nothing should be sent to the
    LLM for a field that was never built in the first place."""
    payload = {"modus_operandi": "Some narrative.", "indicators_triggered": []}
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(narrative_polish, "_call_llm", lambda *a, **k: "Rewritten narrative.")
        results = narrative_polish.polish_narrative_fields(payload)
    assert [r.field for r in results] == ["modus_operandi"]


# --------------------------------------------------------------------------- routes

def test_polish_route_404s_when_the_feature_is_off(analytics_client, token_for, tid, case):
    cid = case(complete=True)
    r = analytics_client.post(
        f"/analytics/{tid}/cases/{cid}/filings/fmr/narrative/polish",
        headers=token_for("supervisor"))
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "not_enabled"


def test_polish_route_requires_a_filing_role_even_when_enabled(analytics_client, token_for,
                                                                tid, case, monkeypatch):
    monkeypatch.setattr(settings, "narrative_polish_enabled", True)
    cid = case(complete=True)
    r = analytics_client.post(
        f"/analytics/{tid}/cases/{cid}/filings/fmr/narrative/polish",
        headers=token_for("analyst"))
    assert r.status_code == 403


def test_polish_route_returns_verified_and_rejected_results(analytics_client, token_for,
                                                             tid, case, monkeypatch):
    monkeypatch.setattr(settings, "narrative_polish_enabled", True)
    monkeypatch.setattr(
        "services.analytics_service.app.filings.narrative_polish._call_llm",
        lambda system_prompt, user_input: "A faithfully rewritten version of: " + user_input.split("\n\n")[0])
    cid = case(complete=True)
    r = analytics_client.post(
        f"/analytics/{tid}/cases/{cid}/filings/fmr/narrative/polish",
        headers=token_for("supervisor"))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["kind"] == "fmr"
    assert len(body["results"]) >= 1
    assert all("field" in r and "verified" in r for r in body["results"])


# ----------------------------------------------------------------- generate() overrides

def test_generate_accepts_a_valid_narrative_override(analytics_client, token_for, tid, case):
    cid = case(complete=True)
    r = analytics_client.post(
        f"/analytics/{tid}/cases/{cid}/filings/fmr/generate",
        headers=token_for("supervisor"),
        json={"modus_operandi": "A human-reviewed, polished narrative for this fraud."})
    assert r.status_code == 200, r.text
    assert r.json()["narrative_overridden"] == ["modus_operandi"]

    db = SessionLocal()
    try:
        row = db.scalars(select(RegulatoryFiling).where(
            RegulatoryFiling.case_id == cid, RegulatoryFiling.kind == "fmr")).first()
    finally:
        db.close()
    assert row.payload["modus_operandi"] == "A human-reviewed, polished narrative for this fraud."


def test_generate_without_any_override_keeps_the_deterministic_narrative(analytics_client,
                                                                          token_for, tid, case):
    """Backward compatibility: a caller that sends no body at all must behave exactly
    as it did before this feature existed."""
    cid = case(complete=True)
    r = analytics_client.post(f"/analytics/{tid}/cases/{cid}/filings/fmr/generate",
                              headers=token_for("supervisor"))
    assert r.status_code == 200, r.text
    assert r.json()["narrative_overridden"] == []


def test_generate_rejects_an_override_field_that_does_not_exist_on_this_kind(analytics_client,
                                                                              token_for, tid, case):
    """suspicion_grounds is an STR-only field — supplying it for an FMR must be
    refused, not silently accepted into a payload it was never meant to carry."""
    cid = case(complete=True)
    r = analytics_client.post(
        f"/analytics/{tid}/cases/{cid}/filings/fmr/generate",
        headers=token_for("supervisor"),
        json={"suspicion_grounds": "This should not be accepted on an FMR."})
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "invalid_override_field"

    db = SessionLocal()
    try:
        n = db.scalar(text("SELECT COUNT(*) FROM cases.regulatory_filings "
                           "WHERE case_id = :c").bindparams(c=cid))
    finally:
        db.close()
    assert n == 0, "a filing was stored despite the invalid override being refused"


def test_generate_records_the_override_in_the_audit_trail(analytics_client, token_for, tid, case):
    cid = case(complete=True)
    analytics_client.post(
        f"/analytics/{tid}/cases/{cid}/filings/fmr/generate",
        headers=token_for("supervisor"),
        json={"modus_operandi": "Human-reviewed narrative."})

    db = SessionLocal()
    try:
        row = db.execute(text(
            "SELECT detail FROM platform.audit_logs WHERE tenant_id = :t "
            "AND action = 'filing.generated' ORDER BY ts DESC LIMIT 1"),
            {"t": tid}).mappings().first()
    finally:
        db.close()
    assert row is not None
    assert row["detail"]["narrative_overridden"] == ["modus_operandi"]
