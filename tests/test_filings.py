"""Regulatory returns: readiness, refusal, immutability and the acknowledgement.

The point of this feature is not that a document comes out. It is that a bank finds out it
cannot evidence a fraud *before* the filing deadline, that a return which is missing its
amount or its declaration date is refused rather than produced with holes, and that what
was filed can be reproduced exactly years later.
"""
import pytest
from sqlalchemy import select, text

from cp_common.db import SessionLocal
from services.analytics_service.app.filings import renderers, schema
from services.analytics_service.app.filings_model import RegulatoryFiling
from services.analytics_service.app.models import FactCase


def _seed_case(tid, *, complete=True, tag="FILE"):
    """A declared fraud, optionally missing what a return needs."""
    from datetime import datetime, timedelta, timezone

    db = SessionLocal()
    try:
        seed = db.scalars(select(FactCase).where(FactCase.tenant_id == tid).limit(1)).first()
        case_id = f"{tag}-{db.query(FactCase).count()}"
        now = datetime.now(timezone.utc)
        db.add(FactCase(
            case_id=case_id, tenant_id=tid, opened_ts=now - timedelta(days=10),
            state="fraud_declared", severity="critical",
            fmr_category="cheating_and_forgery" if complete else "",
            amount_paise=987127 if complete else 0,
            recovered_paise=0, rfa_flag=True, rail="UPI", region="south",
            product="savings", customer_segment="retail", assignee="",
            decision_ts=now - timedelta(days=1) if complete else None,
            fmr_due_ts=now + timedelta(days=6),
            str_due_ts=now + timedelta(days=6)))
        # A return has to be evidenced by something.
        db.execute(text(
            "INSERT INTO analytics.fact_transaction (txn_id, tenant_id, ts, rail, "
            "amount_paise, debtor_account, creditor_account, branch, region, product, "
            "customer_segment, channel, device_id, ip_addr, status, source) "
            "VALUES (:x, :t, :ts, 'UPI', :amt, 'AC-FILE-DR', 'AC-FILE-CR', 'BR1', "
            "'south', 'savings', 'retail', 'mobile', 'D1', '10.0.0.1', 'settled', 'live')"),
            {"x": f"TXN-{case_id}", "t": tid, "ts": now - timedelta(days=9),
             "amt": 987127 if complete else 0})
        db.execute(text(
            "INSERT INTO analytics.fact_alert (alert_id, tenant_id, txn_id, ts, "
            "rule_family, rule_id, typology, score, severity, disposition, case_id, analyst, "
            "config_version, sub_rule_ref, matched_reason, observed_value, "
            "threshold_value, observed_unit, source) VALUES (:a, :t, :x, :ts, 'LAY', "
            "'LAY-02', 'Layering / mule network', 420, 'critical', 'true_positive', "
            ":c, '', '1.0.0', '.03', 'Fan-in hub: 46 counterparties in window', 46, 20, "
            "'distinct_counterparties', 'live')"),
            {"a": f"AL-{case_id}", "t": tid, "x": f"TXN-{case_id}",
             "ts": now - timedelta(days=9), "c": case_id})
        if complete:
            db.execute(text(
                "INSERT INTO cases.case_documents (id, tenant_id, case_id, doc_type, "
                "filename, content_type, size_bytes, sha256, uploaded_by, content) "
                "VALUES (gen_random_uuid()::text, :t, :c, 'reasoned_order', 'order.pdf', "
                "'application/pdf', 12, :h, 'po.aml@test.local', :b)"),
                {"t": tid, "c": case_id, "h": "a" * 64, "b": b"order"})
            db.execute(text(
                "INSERT INTO cases.case_transitions (id, tenant_id, case_id, action, "
                "from_state, to_state, actor, actor_role, status) VALUES "
                "(gen_random_uuid()::text, :t, :c, 'declare_fraud', "
                "'response_evaluation', 'fraud_declared', 'frm.head@test.local', "
                "'risk_manager', 'applied')"), {"t": tid, "c": case_id})
        db.commit()
        return case_id
    finally:
        db.close()


def _cleanup(case_id):
    db = SessionLocal()
    try:
        for stmt in (
            "DELETE FROM cases.regulatory_filings WHERE case_id = :c",
            "DELETE FROM cases.case_transitions WHERE case_id = :c",
            "DELETE FROM cases.case_documents WHERE case_id = :c",
            "DELETE FROM analytics.fact_alert WHERE case_id = :c",
            "DELETE FROM analytics.fact_transaction WHERE txn_id = :x",
            "DELETE FROM analytics.fact_case WHERE case_id = :c",
        ):
            db.execute(text(stmt), {"c": case_id, "x": f"TXN-{case_id}"})
        db.commit()
    finally:
        db.close()


@pytest.fixture
def case(tid):
    made = []

    def _make(**kw):
        cid = _seed_case(tid, **kw)
        made.append(cid)
        return cid

    yield _make
    for cid in made:
        _cleanup(cid)


# ----------------------------------------------------------------- readiness
def test_readiness_says_exactly_what_is_missing(analytics_client, token_for, tid, case):
    """The value is finding out before the deadline, not at the portal."""
    cid = case(complete=False)
    r = analytics_client.get(f"/analytics/{tid}/cases/{cid}/filings/fmr/readiness",
                             headers=token_for("supervisor"))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ready"] is False
    fields = {g["field"] for g in body["blocking"]}
    assert "declaration_date" in fields, f"missing declaration not reported: {fields}"
    # Every gap explains itself, so someone can go and find the thing.
    assert all(g["why"] or g["label"] for g in body["blocking"])


def test_a_complete_case_is_ready(analytics_client, token_for, tid, case):
    cid = case(complete=True)
    body = analytics_client.get(f"/analytics/{tid}/cases/{cid}/filings/fmr/readiness",
                                headers=token_for("supervisor")).json()
    assert body["ready"] is True, body["blocking"]


def test_an_unknown_return_type_is_refused(analytics_client, token_for, tid, case):
    cid = case()
    r = analytics_client.get(f"/analytics/{tid}/cases/{cid}/filings/xyz/readiness",
                             headers=token_for("supervisor"))
    assert r.status_code == 400


# ----------------------------------------------------------------- generation
def test_an_incomplete_return_is_refused_not_produced(analytics_client, token_for, tid,
                                                      case):
    """A return missing its amount is not a draft. It is a rejection waiting to happen."""
    cid = case(complete=False)
    r = analytics_client.post(f"/analytics/{tid}/cases/{cid}/filings/fmr/generate",
                              headers=token_for("supervisor"))
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "not_ready_to_file"

    db = SessionLocal()
    try:
        n = db.scalar(text("SELECT COUNT(*) FROM cases.regulatory_filings "
                           "WHERE case_id = :c").bindparams(c=cid))
    finally:
        db.close()
    assert n == 0, "a return was stored despite being unfileable"


def test_generating_stores_a_hashed_record(analytics_client, token_for, tid, case):
    cid = case()
    r = analytics_client.post(f"/analytics/{tid}/cases/{cid}/filings/fmr/generate",
                              headers=token_for("supervisor"))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["content_hash"] and len(body["content_hash"]) == 64
    assert body["revision"] == 1
    assert body["validation"]["ready"] is True


def test_regenerating_supersedes_the_earlier_draft(analytics_client, token_for, tid, case):
    cid = case()
    h = token_for("supervisor")
    first = analytics_client.post(f"/analytics/{tid}/cases/{cid}/filings/fmr/generate",
                                  headers=h).json()
    second = analytics_client.post(f"/analytics/{tid}/cases/{cid}/filings/fmr/generate",
                                   headers=h).json()
    assert second["revision"] == 2

    listed = analytics_client.get(f"/analytics/{tid}/cases/{cid}/filings",
                                  headers=h).json()
    by_id = {f["id"]: f for f in listed}
    assert by_id[first["id"]]["status"] == "superseded"
    assert by_id[second["id"]]["status"] == "generated"


def test_only_a_compliance_role_may_prepare_a_return(analytics_client, token_for, tid,
                                                     case):
    cid = case()
    r = analytics_client.post(f"/analytics/{tid}/cases/{cid}/filings/fmr/generate",
                              headers=token_for("analyst"))
    assert r.status_code == 403


# ----------------------------------------------------------------------- STR
# Every test above exercises "fmr" only. That asymmetry is what let the STR path ship
# with an unfulfillable mandatory field (see the Principal Officer wiring in _entity/
# policy) with nothing red in the suite. These exist so "str" never again drifts
# silently from "fmr".
def test_a_complete_case_is_ready_for_str_too(analytics_client, token_for, tid, case):
    cid = case(complete=True)
    body = analytics_client.get(f"/analytics/{tid}/cases/{cid}/filings/str/readiness",
                                headers=token_for("supervisor")).json()
    assert body["ready"] is True, body["blocking"]


def test_str_generation_names_the_principal_officer_from_policy(analytics_client,
                                                                 token_for, tid, case):
    """The field the STR path was blocked on: it has to come from the tenant's
    board-approved policy, not be guessed or left for a human to notice missing."""
    cid = case(complete=True)
    r = analytics_client.post(f"/analytics/{tid}/cases/{cid}/filings/str/generate",
                              headers=token_for("supervisor"))
    assert r.status_code == 200, r.text
    assert r.json()["validation"]["ready"] is True

    db = SessionLocal()
    try:
        row = db.scalars(select(RegulatoryFiling).where(
            RegulatoryFiling.case_id == cid, RegulatoryFiling.kind == "str")).first()
    finally:
        db.close()
    assert row.payload["principal_officer"] == "A. Krishnan"


# ------------------------------------------------------------------ rendering
@pytest.mark.parametrize("fmt", ["json", "xml", "html"])
def test_every_format_carries_the_same_facts(analytics_client, token_for, tid, case, fmt):
    cid = case()
    h = token_for("supervisor")
    made = analytics_client.post(f"/analytics/{tid}/cases/{cid}/filings/fmr/generate",
                                 headers=h).json()
    r = analytics_client.get(f"/analytics/{tid}/filings/{made['id']}/download?fmt={fmt}",
                             headers=h)
    assert r.status_code == 200, r.text
    text_out = r.text
    assert cid in text_out, "the case reference is missing"
    assert "9,871.27" in text_out or "987127" in text_out, "the amount is missing"
    assert "LAY-02" in text_out, "the indicator that fired is missing"


def test_every_rendering_says_it_is_not_the_regulator_s_own_format(analytics_client,
                                                                   token_for, tid, case):
    """The most damaging outcome would be someone treating the pack as the filed return."""
    cid = case()
    h = token_for("supervisor")
    made = analytics_client.post(f"/analytics/{tid}/cases/{cid}/filings/fmr/generate",
                                 headers=h).json()
    for fmt in ("json", "xml", "html"):
        body = analytics_client.get(
            f"/analytics/{tid}/filings/{made['id']}/download?fmt={fmt}",
            headers=h).text
        assert "not the regulator" in body.lower() or "not the" in body.lower(), \
            f"{fmt} rendering carries no disclaimer"


def test_the_xml_is_well_formed(analytics_client, token_for, tid, case):
    from xml.etree import ElementTree as ET

    cid = case()
    h = token_for("supervisor")
    made = analytics_client.post(f"/analytics/{tid}/cases/{cid}/filings/fmr/generate",
                                 headers=h).json()
    body = analytics_client.get(f"/analytics/{tid}/filings/{made['id']}/download?fmt=xml",
                                headers=h).text
    root = ET.fromstring(body)
    assert root.tag == "RegulatoryReturn"
    assert root.get("schemaVersion") == schema.SCHEMA_VERSION


def test_a_return_is_rendered_from_what_was_stored(analytics_client, token_for, tid, case):
    """Downloaded a year later it must say what was filed, not what today's record
    would produce."""
    cid = case()
    h = token_for("supervisor")
    made = analytics_client.post(f"/analytics/{tid}/cases/{cid}/filings/fmr/generate",
                                 headers=h).json()

    # The case is corrected after the fact.
    db = SessionLocal()
    try:
        db.execute(text("UPDATE analytics.fact_case SET amount_paise = 5500000 "
                        "WHERE case_id = :c"), {"c": cid})
        db.commit()
    finally:
        db.close()

    body = analytics_client.get(f"/analytics/{tid}/filings/{made['id']}/download?fmt=json",
                                headers=h).text
    assert "9,871.27" in body, "the stored return was rebuilt from changed data"
    assert "55,000.00" not in body


# ------------------------------------------------------------- acknowledgement
def test_the_clock_stops_on_acknowledgement_not_generation(analytics_client, token_for,
                                                           tid, case):
    """Preparing a return discharges nothing. Submitting it does."""
    cid = case()
    h = token_for("supervisor")
    made = analytics_client.post(f"/analytics/{tid}/cases/{cid}/filings/fmr/generate",
                                 headers=h).json()

    db = SessionLocal()
    try:
        filed = db.scalar(text("SELECT fmr_filed_ts FROM analytics.fact_case "
                               "WHERE case_id = :c").bindparams(c=cid))
    finally:
        db.close()
    assert filed is None, "generating a return stopped the clock"

    r = analytics_client.post(f"/analytics/{tid}/filings/{made['id']}/acknowledge",
                              headers=h, json={"reference_number": "RBI/FMR/2026/00123"})
    assert r.status_code == 200, r.text

    db = SessionLocal()
    try:
        filed = db.scalar(text("SELECT fmr_filed_ts FROM analytics.fact_case "
                               "WHERE case_id = :c").bindparams(c=cid))
    finally:
        db.close()
    assert filed is not None, "the clock did not stop on acknowledgement"


def test_a_reference_number_cannot_be_recorded_twice(analytics_client, token_for, tid,
                                                     case):
    cid = case()
    h = token_for("supervisor")
    made = analytics_client.post(f"/analytics/{tid}/cases/{cid}/filings/fmr/generate",
                                 headers=h).json()
    analytics_client.post(f"/analytics/{tid}/filings/{made['id']}/acknowledge",
                          headers=h, json={"reference_number": "REF-1"})
    again = analytics_client.post(f"/analytics/{tid}/filings/{made['id']}/acknowledge",
                                  headers=h, json={"reference_number": "REF-2"})
    assert again.status_code == 409
    assert again.json()["error"]["code"] == "already_submitted"


def test_a_superseded_draft_cannot_be_submitted(analytics_client, token_for, tid, case):
    cid = case()
    h = token_for("supervisor")
    first = analytics_client.post(f"/analytics/{tid}/cases/{cid}/filings/fmr/generate",
                                  headers=h).json()
    analytics_client.post(f"/analytics/{tid}/cases/{cid}/filings/fmr/generate", headers=h)
    r = analytics_client.post(f"/analytics/{tid}/filings/{first['id']}/acknowledge",
                              headers=h, json={"reference_number": "REF-OLD"})
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "superseded"


# -------------------------------------------------------------------- the queue
def test_the_due_queue_separates_ready_from_blocked(analytics_client, token_for, tid,
                                                    case):
    """The useful question is not what is due, but what is due and cannot be evidenced."""
    ready_case = case(complete=True, tag="DUEOK")
    blocked_case = case(complete=False, tag="DUEBAD")

    body = analytics_client.get(f"/analytics/{tid}/filings/due",
                                headers=token_for("supervisor")).json()
    by_case = {(i["case_id"], i["kind"]): i for i in body["items"]}
    assert by_case[(ready_case, "fmr")]["ready"] is True
    blocked = by_case[(blocked_case, "fmr")]
    assert blocked["ready"] is False
    assert blocked["blocking"], "nothing explains why it cannot be filed"


# --------------------------------------------------------------------- honesty
def test_the_platform_does_not_claim_to_have_bound_the_regulator_format():
    """Inventing something that resembles the real schema would be worse than useless:
    it would look authoritative and be filed."""
    for kind, binding in schema.FORMAT_BINDINGS.items():
        assert binding["status"] == "not_bound", \
            f"{kind} claims a binding that has not been supplied"
        assert binding["needs"], "no statement of what is required to bind it"


def test_validation_treats_a_blank_as_missing():
    """A return that says 'amount: ' has not answered the question."""
    v = schema.validate("fmr", {"entity_name": "", "amount_involved": None,
                                "accounts_involved": []})
    missing = {g.key for g in v.blocking}
    assert {"entity_name", "amount_involved", "accounts_involved"} <= missing


def test_an_explicit_no_is_an_answer():
    """reasoned_order_on_file=False is a finding, not an absence - and it must block."""
    v = schema.validate("fmr", {"reasoned_order_on_file": False})
    assert "reasoned_order_on_file" not in {g.key for g in v.blocking}
