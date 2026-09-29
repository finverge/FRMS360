"""LEA referral and recovery (BR-415).

``fact_case.recovered_paise`` feeds the net-loss figure, the board pack and every
dashboard, and until now nobody had to substantiate it. These tests hold two lines: a
recovery is an entry with a reference behind it, and a figure with nothing behind it is
reported as unevidenced rather than quietly accepted.
"""
from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import select, text

from cp_common.db import SessionLocal
from services.analytics_service.app.lea_model import LeaReferral, RecoveryEntry
from services.analytics_service.app.models import FactCase


def _seed_case(tid, *, state="fraud_declared", amount=5_00_00_000, recovered=0,
               tag="REC"):
    db = SessionLocal()
    try:
        case_id = f"{tag}-{db.query(FactCase).count()}"
        now = datetime.now(timezone.utc)
        db.add(FactCase(
            case_id=case_id, tenant_id=tid, opened_ts=now - timedelta(days=20),
            state=state, severity="critical", fmr_category="cheating_and_forgery",
            amount_paise=amount, recovered_paise=recovered, rfa_flag=True, rail="UPI",
            region="south", product="savings", customer_segment="retail", assignee="",
            decision_ts=now - timedelta(days=3)))
        db.commit()
        return case_id
    finally:
        db.close()


def _cleanup(case_id):
    db = SessionLocal()
    try:
        for stmt in ("DELETE FROM cases.recovery_entries WHERE case_id = :c",
                     "DELETE FROM cases.lea_referrals WHERE case_id = :c",
                     "DELETE FROM analytics.fact_case WHERE case_id = :c"):
            db.execute(text(stmt), {"c": case_id})
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


def _url(tid, cid, suffix=""):
    return f"/analytics/{tid}/cases/{cid}/recovery{suffix}"


def _refer(client, token_for, tid, cid, agency="eow", role="risk_manager", on=None):
    return client.post(_url(tid, cid, "/referral"), headers=token_for(role), json={
        "agency": agency, "agency_office": "Mumbai EOW",
        "complaint_ref": "CMP/2026/8891",
        "referred_on": (on or date.today()).isoformat(),
        "note": "Complaint lodged with supporting statements."})


def _recover(client, token_for, tid, cid, amount, *, mode="insurance",
             role="risk_manager", on=None):
    return client.post(_url(tid, cid, "/entries"), headers=token_for(role), json={
        "amount_paise": amount, "mode": mode, "reference": "CLM-77123",
        "recovered_on": (on or date.today()).isoformat(), "note": ""})


# ------------------------------------------------------------------ referral
def test_a_case_is_not_referred_before_fraud_is_declared(analytics_client, token_for,
                                                         tid, case):
    cid = case(state="under_review")
    r = _refer(analytics_client, token_for, tid, cid)
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "not_declared"


def test_referral_records_the_agency_and_complaint(analytics_client, token_for, tid,
                                                   case):
    cid = case()
    body = _refer(analytics_client, token_for, tid, cid).json()
    assert body["referral"]["agency"] == "eow"
    assert body["referral"]["agency_label"] == "Economic Offences Wing"
    assert body["referral"]["state"] == "referred"
    assert body["referral"]["progressed"] is False


def test_an_unknown_agency_is_refused(analytics_client, token_for, tid, case):
    cid = case()
    r = _refer(analytics_client, token_for, tid, cid, agency="interpol")
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "unknown_agency"


def test_a_referral_cannot_be_dated_in_the_future(analytics_client, token_for, tid, case):
    cid = case()
    r = _refer(analytics_client, token_for, tid, cid,
               on=date.today() + timedelta(days=2))
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "future_date"


def test_recording_an_fir_requires_its_number(analytics_client, token_for, tid, case):
    """The one fact the agency will ask for. An FIR without it cannot be followed up."""
    cid = case()
    _refer(analytics_client, token_for, tid, cid)
    r = analytics_client.post(_url(tid, cid, "/referral/update"),
                              headers=token_for("risk_manager"),
                              json={"state": "fir_registered"})
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "fir_number_required"

    ok = analytics_client.post(_url(tid, cid, "/referral/update"),
                               headers=token_for("risk_manager"),
                               json={"state": "fir_registered",
                                     "fir_number": "FIR/0442/2026",
                                     "fir_date": date.today().isoformat()})
    assert ok.status_code == 200, ok.text
    assert ok.json()["referral"]["progressed"] is True


def test_updating_needs_an_existing_referral(analytics_client, token_for, tid, case):
    cid = case()
    r = analytics_client.post(_url(tid, cid, "/referral/update"),
                              headers=token_for("risk_manager"),
                              json={"state": "under_investigation"})
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "not_referred"


# ------------------------------------------------------------------ recovery
def test_a_recovery_updates_the_case_total_from_its_entries(analytics_client, token_for,
                                                            tid, case):
    """The column every dashboard reads becomes the sum of substantiated entries."""
    cid = case(amount=1_000_000, recovered=0)
    b1 = _recover(analytics_client, token_for, tid, cid, 400_000).json()
    assert b1["recovered_paise"] == 400_000
    assert b1["recovery_evidenced"] is True

    b2 = _recover(analytics_client, token_for, tid, cid, 250_000,
                  mode="court_order").json()
    assert b2["recovered_paise"] == 650_000
    assert b2["recovery_entries_paise"] == 650_000
    assert b2["outstanding_paise"] == 350_000

    db = SessionLocal()
    try:
        stored = db.scalars(select(FactCase).where(FactCase.case_id == cid)).one()
        assert stored.recovered_paise == 650_000, "the case column was not re-derived"
    finally:
        db.close()


def test_recovering_more_than_was_lost_is_refused(analytics_client, token_for, tid, case):
    """It is a data error every time, and it would make net fraud value negative."""
    cid = case(amount=1_000_000)
    assert _recover(analytics_client, token_for, tid, cid, 900_000).status_code == 200
    r = _recover(analytics_client, token_for, tid, cid, 200_000)
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "exceeds_loss"


def test_an_unknown_recovery_mode_is_refused(analytics_client, token_for, tid, case):
    cid = case()
    r = _recover(analytics_client, token_for, tid, cid, 1000, mode="hoping")
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "unknown_mode"


def test_a_write_off_is_not_an_available_recovery_mode(analytics_client, token_for, tid,
                                                       case):
    """Writing a loss off is not recovering it, and recording it as one flatters the
    net-loss figure the board reads."""
    from services.analytics_service.app.lea_model import RECOVERY_MODES
    assert not any("write" in m for m in RECOVERY_MODES)
    cid = case()
    assert _recover(analytics_client, token_for, tid, cid, 1000,
                    mode="write_off").status_code == 400


def test_a_zero_or_negative_recovery_is_refused(analytics_client, token_for, tid, case):
    cid = case()
    for amount in (0, -5000):
        r = analytics_client.post(_url(tid, cid, "/entries"),
                                  headers=token_for("risk_manager"),
                                  json={"amount_paise": amount, "mode": "insurance",
                                        "recovered_on": date.today().isoformat()})
        assert r.status_code == 422, f"{amount} paise was accepted"


def test_a_recovery_cannot_be_dated_in_the_future(analytics_client, token_for, tid, case):
    cid = case()
    r = _recover(analytics_client, token_for, tid, cid, 1000,
                 on=date.today() + timedelta(days=1))
    assert r.status_code == 400


# ------------------------------------------------- the unevidenced figure
def test_a_pre_existing_recovered_figure_is_reported_as_unevidenced(
        analytics_client, token_for, tid, case):
    """Historical rows carry a recovered amount with nothing behind it.

    That is not corrected silently and it is not wiped. It is reported, because a
    recovery nobody can produce a reference for is a finding.
    """
    cid = case(amount=1_000_000, recovered=300_000)
    body = analytics_client.get(_url(tid, cid),
                                headers=token_for("supervisor")).json()
    assert body["recovered_paise"] == 300_000
    assert body["recovery_entries_paise"] == 0
    assert body["recovery_evidenced"] is False
    assert body["unevidenced_paise"] == 300_000


def test_evidencing_a_pre_existing_figure_closes_the_gap(analytics_client, token_for,
                                                         tid, case):
    cid = case(amount=1_000_000, recovered=300_000)
    body = _recover(analytics_client, token_for, tid, cid, 300_000).json()
    assert body["recovery_evidenced"] is True
    assert body["unevidenced_paise"] == 0
    assert body["recovered_paise"] == 300_000


# ------------------------------------------------------------------ register
def test_the_register_surfaces_frauds_over_the_floor_with_no_complaint(
        analytics_client, token_for, tid, case):
    """The gap worth reporting: a large fraud nobody told the police about."""
    floor = analytics_client.get(f"/analytics/{tid}/recovery/register",
                                 headers=token_for("supervisor")).json()
    big = case(amount=max(floor["referral_floor_paise"], 1) + 1_000_000, tag="RECBIG")
    small = case(amount=1000, tag="RECSML")

    reg = analytics_client.get(f"/analytics/{tid}/recovery/register",
                               headers=token_for("supervisor")).json()
    rows = {i["case_id"]: i for i in reg["items"]}
    assert rows[big]["referral_expected"] is True
    assert rows[big]["referral_missing"] is True
    assert rows[small]["referral_expected"] is False
    assert rows[small]["referral_missing"] is False
    # Worst first.
    assert reg["items"][0]["referral_missing"] is True

    _refer(analytics_client, token_for, tid, big)
    after = analytics_client.get(f"/analytics/{tid}/recovery/register",
                                 headers=token_for("supervisor")).json()
    assert after["referral_missing"] == reg["referral_missing"] - 1
    assert {i["case_id"]: i for i in after["items"]}[big]["referral_stalled"] is True


def test_the_register_counts_reconcile_with_its_own_rows(analytics_client, token_for,
                                                         tid, case):
    case(amount=1_000_000, recovered=250_000, tag="RECUN")
    reg = analytics_client.get(f"/analytics/{tid}/recovery/register",
                               headers=token_for("supervisor")).json()
    items = reg["items"]
    assert reg["count"] == len(items)
    assert reg["referral_missing"] == sum(1 for i in items if i["referral_missing"])
    assert reg["unevidenced_cases"] == sum(
        1 for i in items if not i["recovery_evidenced"])
    assert reg["unevidenced_paise"] == sum(i["unevidenced_paise"] for i in items)
    assert reg["unevidenced_cases"] >= 1


def test_recovery_is_scoped_to_its_tenant(analytics_client, token_for, tid, case):
    cid = case()
    r = analytics_client.get(_url("00000000-0000-0000-0000-000000000000", cid),
                             headers=token_for("supervisor"))
    assert r.status_code in (403, 404)


def test_an_analyst_may_read_but_not_record(analytics_client, token_for, tid, case):
    cid = case()
    assert analytics_client.get(_url(tid, cid),
                                headers=token_for("analyst")).status_code == 200
    assert _refer(analytics_client, token_for, tid, cid,
                  role="analyst").status_code == 403
    assert _recover(analytics_client, token_for, tid, cid, 1000,
                    role="analyst").status_code == 403


def test_entries_are_stored_against_the_case(analytics_client, token_for, tid, case):
    cid = case(amount=1_000_000)
    _recover(analytics_client, token_for, tid, cid, 100_000)
    db = SessionLocal()
    try:
        rows = db.scalars(select(RecoveryEntry).where(
            RecoveryEntry.case_id == cid)).all()
        assert len(rows) == 1
        assert rows[0].tenant_id == tid
        assert rows[0].amount_paise == 100_000
    finally:
        db.close()
