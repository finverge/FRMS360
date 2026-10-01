"""LNC-15/LNC-16/LNC-21 - the manual-finding entry point, and the signal functions that
read it. Pure-math tests for the signal functions (mirrors test_lane_c_signals.py),
plus a real API round-trip proving submission -> runner lookup -> signal actually works.
"""
from datetime import date

import pytest
from sqlalchemy import text

from cp_common.db import SessionLocal
from services.lane_c_service.app.runner import _manual_finding
from services.lane_c_service.app.signals import manual_findings as mf

ACC = "AC-LNC15-BR214"


@pytest.fixture(autouse=True)
def _clean(tid):
    yield
    db = SessionLocal()
    try:
        db.execute(text("DELETE FROM lane_c.manual_findings WHERE tenant_id = :t"),
                   {"t": tid})
        db.commit()
    finally:
        db.close()


# ------------------------------------------------------------------ pure signal functions
def test_no_entry_is_unmeasurable():
    assert mf.compute_godown_inspection_check(None).status == "unmeasurable"
    assert mf.compute_bill_verification_check(None).status == "unmeasurable"
    assert mf.compute_invoice_compliance_check(None).status == "unmeasurable"


def test_a_recorded_clean_check_passes():
    r = mf.compute_godown_inspection_check({"finding": False, "notes": ""})
    assert r.status == "pass"
    assert r.severity == 0


def test_a_recorded_finding_fires():
    r = mf.compute_godown_inspection_check(
        {"finding": True, "notes": "Inspection postponed twice with no credible reason."})
    assert r.status == "high"
    assert r.severity == 45
    assert r.signal_code == "LNC-15"
    assert "postponed twice" in r.evidence
    assert r.evidence_basis == "manual"


def test_a_finding_with_no_notes_gets_a_default_evidence_string():
    r = mf.compute_bill_verification_check({"finding": True, "notes": ""})
    assert r.status == "high"
    assert r.signal_code == "LNC-16"
    assert "bills" in r.evidence.lower()


def test_a_clean_invoice_sample_passes():
    r = mf.compute_invoice_compliance_check({"finding": False, "notes": ""})
    assert r.status == "pass"
    assert r.severity == 0
    assert r.signal_code == "LNC-21"


def test_a_recorded_invoice_finding_fires():
    r = mf.compute_invoice_compliance_check(
        {"finding": True, "notes": "Sampled invoices missing TAN."})
    assert r.status == "high"
    assert r.severity == 45
    assert r.signal_code == "LNC-21"
    assert "missing TAN" in r.evidence
    assert r.evidence_basis == "manual"


# ------------------------------------------------------------------ API + runner round-trip
def _submit(client, token_for, tid, account, role="tenant_admin", **kwargs):
    payload = {"reporting_date": "2024-03-31", "signal_code": "LNC-15", "finding": True,
              "notes": "Test finding"}
    payload.update(kwargs)
    return client.post(f"/lane-c/{tid}/borrowers/{account}/manual-finding",
                       headers=token_for(role), data=payload)


def test_an_unknown_signal_code_is_refused(lane_c_client, token_for, tid):
    r = _submit(lane_c_client, token_for, tid, ACC, signal_code="LNC-99")
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "invalid_signal_code"


def test_submission_is_readable_by_the_runners_own_lookup(lane_c_client, token_for, tid):
    r = _submit(lane_c_client, token_for, tid, ACC)
    assert r.status_code == 200, r.text
    assert r.json()["resubmission"] is False

    db = SessionLocal()
    try:
        entry = _manual_finding(db, tid, ACC, date(2024, 3, 31), "LNC-15")
    finally:
        db.close()
    assert entry == {"finding": True, "notes": "Test finding"}


def test_resubmission_for_the_same_period_overwrites_not_duplicates(lane_c_client,
                                                                     token_for, tid):
    _submit(lane_c_client, token_for, tid, ACC, notes="First pass")
    r2 = _submit(lane_c_client, token_for, tid, ACC, notes="Corrected note", finding=False)
    assert r2.json()["resubmission"] is True

    db = SessionLocal()
    try:
        entry = _manual_finding(db, tid, ACC, date(2024, 3, 31), "LNC-15")
        count = db.execute(text(
            "SELECT count(*) FROM lane_c.manual_findings WHERE tenant_id = :t AND account = :a"),
            {"t": tid, "a": ACC}).scalar()
    finally:
        db.close()
    assert entry == {"finding": False, "notes": "Corrected note"}
    assert count == 1


def test_a_different_period_is_a_separate_finding_not_an_overwrite(lane_c_client,
                                                                    token_for, tid):
    _submit(lane_c_client, token_for, tid, ACC, reporting_date="2024-03-31")
    _submit(lane_c_client, token_for, tid, ACC, reporting_date="2024-06-30", finding=False)

    db = SessionLocal()
    try:
        q1 = _manual_finding(db, tid, ACC, date(2024, 3, 31), "LNC-15")
        q2 = _manual_finding(db, tid, ACC, date(2024, 6, 30), "LNC-15")
    finally:
        db.close()
    assert q1["finding"] is True
    assert q2["finding"] is False


def test_lnc21_is_accepted_and_readable_by_the_runners_own_lookup(lane_c_client,
                                                                   token_for, tid):
    r = _submit(lane_c_client, token_for, tid, ACC, signal_code="LNC-21",
               notes="Sampled invoices missing TAN.")
    assert r.status_code == 200, r.text

    db = SessionLocal()
    try:
        entry = _manual_finding(db, tid, ACC, date(2024, 3, 31), "LNC-21")
    finally:
        db.close()
    assert entry == {"finding": True, "notes": "Sampled invoices missing TAN."}
