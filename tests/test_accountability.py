"""Staff accountability examination (BR-414).

The obligation these tests protect is not "a record exists". It is that a bank cannot file
a fraud away with the question of its own staff's conduct never asked, that asking the
question is never allowed to delay the regulatory return, and that once the answer is
given it cannot be quietly rewritten.
"""
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select, text

from cp_common.db import SessionLocal
from services.analytics_service.app import workflow as wf
from services.analytics_service.app.accountability_model import (
    AccountabilityFinding, StaffAccountability,
)
from services.analytics_service.app.models import FactCase


def _seed_case(tid, *, state="fraud_declared", due_in_days=180, tag="ACC"):
    db = SessionLocal()
    try:
        case_id = f"{tag}-{db.query(FactCase).count()}"
        now = datetime.now(timezone.utc)
        db.add(FactCase(
            case_id=case_id, tenant_id=tid, opened_ts=now - timedelta(days=30),
            state=state, severity="critical", fmr_category="cheating_and_forgery",
            amount_paise=5_00_00_000, recovered_paise=0, rfa_flag=True, rail="UPI",
            region="south", product="savings", customer_segment="retail", assignee="",
            decision_ts=now - timedelta(days=5),
            accountability_due_ts=now + timedelta(days=due_in_days)))
        db.commit()
        return case_id
    finally:
        db.close()


def _cleanup(case_id):
    db = SessionLocal()
    try:
        for stmt in (
            "DELETE FROM cases.accountability_findings WHERE case_id = :c",
            "DELETE FROM cases.staff_accountability WHERE case_id = :c",
            "DELETE FROM cases.case_transitions WHERE case_id = :c",
            "DELETE FROM analytics.fact_case WHERE case_id = :c",
        ):
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
    return f"/analytics/{tid}/cases/{cid}/accountability{suffix}"


def _open(client, token_for, tid, cid, role="risk_manager"):
    return client.post(_url(tid, cid, "/open"), json={"examiner": "vigilance@test.local"},
                       headers=token_for(role))


def _finding(client, token_for, tid, cid, *, finding="procedural_lapse",
             ref="EMP-1001", role="risk_manager"):
    return client.post(_url(tid, cid, "/findings"), headers=token_for(role), json={
        "staff_ref": ref, "staff_name": "A. Nair", "role_at_time": "Branch Manager",
        "branch": "Andheri West", "finding": finding, "action_taken": "disciplinary",
        "note": "Overrode the dual-authorisation control on four transfers."})


# ------------------------------------------------------- when it may be examined
def test_accountability_is_not_examined_before_fraud_is_declared(
        analytics_client, token_for, tid, case):
    """Examining staff over an unsustained allegation prejudges it."""
    cid = case(state="under_review")
    r = _open(analytics_client, token_for, tid, cid)
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "not_declared"


def test_opening_twice_is_refused(analytics_client, token_for, tid, case):
    """Two examinations would make 'was this examined?' ambiguous."""
    cid = case()
    assert _open(analytics_client, token_for, tid, cid).status_code == 200
    assert _open(analytics_client, token_for, tid, cid).status_code == 409


def test_an_unexamined_case_reports_no_status_not_a_default(
        analytics_client, token_for, tid, case):
    """"Nobody started" must be distinguishable from "started and going nowhere"."""
    cid = case()
    body = analytics_client.get(_url(tid, cid),
                                headers=token_for("supervisor")).json()
    assert body["status"] == ""
    assert body["required"] is True
    assert body["window_days"] == 180


# ------------------------------------------------------- the findings themselves
def test_a_finding_records_the_person_and_what_was_done(
        analytics_client, token_for, tid, case):
    cid = case()
    _open(analytics_client, token_for, tid, cid)
    body = _finding(analytics_client, token_for, tid, cid).json()
    assert len(body["findings"]) == 1
    f = body["findings"][0]
    assert f["staff_ref"] == "EMP-1001"
    assert f["adverse"] is True
    assert f["finding_label"] == "Procedural lapse - control not followed"
    assert body["staff_implicated"] == 1


def test_no_lapse_is_a_finding_not_an_absence(analytics_client, token_for, tid, case):
    """A fraud can happen with nobody at fault; that has to be recordable."""
    cid = case()
    _open(analytics_client, token_for, tid, cid)
    body = _finding(analytics_client, token_for, tid, cid, finding="no_lapse").json()
    assert body["findings"][0]["adverse"] is False
    assert body["staff_implicated"] == 0


def test_an_unknown_finding_is_refused(analytics_client, token_for, tid, case):
    cid = case()
    _open(analytics_client, token_for, tid, cid)
    r = _finding(analytics_client, token_for, tid, cid, finding="probably_fine")
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "unknown_finding"


def test_a_finding_needs_an_open_examination(analytics_client, token_for, tid, case):
    cid = case()
    r = _finding(analytics_client, token_for, tid, cid)
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "not_open"


# ------------------------------------------------------- concluding
def test_concluding_with_no_findings_is_refused(analytics_client, token_for, tid, case):
    """An examination with nothing in it cannot be told apart from one never held."""
    cid = case()
    _open(analytics_client, token_for, tid, cid)
    r = analytics_client.post(_url(tid, cid, "/conclude"),
                              headers=token_for("risk_manager"),
                              json={"conclusion": "Nothing to report."})
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "no_findings"


def test_concluding_freezes_the_findings(analytics_client, token_for, tid, case):
    cid = case()
    _open(analytics_client, token_for, tid, cid)
    _finding(analytics_client, token_for, tid, cid)
    r = analytics_client.post(_url(tid, cid, "/conclude"),
                              headers=token_for("risk_manager"),
                              json={"conclusion": "Branch manager at fault.",
                                    "systemic_lapse": True,
                                    "systemic_note": "Dual auth not enforced by the CBS."})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "concluded"
    assert body["systemic_lapse"] is True

    # No further findings, and no withdrawals.
    assert _finding(analytics_client, token_for, tid, cid,
                    ref="EMP-2002").status_code == 409
    fid = body["findings"][0]["id"]
    assert analytics_client.delete(
        _url(tid, cid, f"/findings/{fid}"),
        headers=token_for("risk_manager")).status_code == 409


def test_a_supervisor_may_examine_but_not_conclude(analytics_client, token_for, tid,
                                                   case):
    """Gathering findings is casework; signing them off is an act of authority."""
    cid = case()
    assert _open(analytics_client, token_for, tid, cid, role="supervisor").status_code == 200
    assert _finding(analytics_client, token_for, tid, cid,
                    role="supervisor").status_code == 200
    r = analytics_client.post(_url(tid, cid, "/conclude"), headers=token_for("supervisor"),
                              json={"conclusion": "Signed off."})
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "role_not_permitted"


def test_the_payload_declares_who_may_conclude(analytics_client, token_for, tid, case):
    """So the console can disable the control with its reason, per BR-410.

    A button that is offered and then refused on save teaches nothing; one that is
    disabled saying who is needed teaches the separation of duties.
    """
    cid = case()
    sup = analytics_client.get(_url(tid, cid), headers=token_for("supervisor")).json()
    assert sup["may_examine"] is True
    assert sup["may_conclude"] is False
    assert "Fraud Risk Manager" in sup["conclude_requires"]

    rm = analytics_client.get(_url(tid, cid), headers=token_for("risk_manager")).json()
    assert rm["may_conclude"] is True


def test_concluding_late_is_recorded_not_blocked(analytics_client, token_for, tid, case):
    """Same treatment the show-cause notice gets: late is a fact, not a refusal."""
    cid = case(due_in_days=-10)
    _open(analytics_client, token_for, tid, cid)
    _finding(analytics_client, token_for, tid, cid)
    body = analytics_client.post(_url(tid, cid, "/conclude"),
                                 headers=token_for("risk_manager"),
                                 json={"conclusion": "Concluded after the window."}).json()
    assert body["status"] == "concluded"
    assert body["breached_policy"] is True, "a late conclusion was not marked as a breach"
    assert body["breach_days_allowed"] == 180


def test_a_withdrawn_finding_is_gone_while_the_examination_is_open(
        analytics_client, token_for, tid, case):
    cid = case()
    _open(analytics_client, token_for, tid, cid)
    fid = _finding(analytics_client, token_for, tid, cid).json()["findings"][0]["id"]
    body = analytics_client.delete(_url(tid, cid, f"/findings/{fid}"),
                                   headers=token_for("risk_manager")).json()
    assert body["findings"] == []


# ------------------------------------------------------- the workflow coupling
def test_a_fraud_cannot_be_closed_with_accountability_unexamined(
        analytics_client, token_for, tid, case):
    """The obligation this whole feature exists to enforce."""
    cid = case(state="fmr_reported")
    r = analytics_client.post(f"/analytics/{tid}/cases/{cid}/transition",
                              headers=token_for("risk_manager"),
                              json={"action": "close_case", "reason": "Done."})
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "accountability_pending"


def test_closing_is_permitted_once_the_examination_concludes(
        analytics_client, token_for, tid, case):
    cid = case(state="fmr_reported")
    _open(analytics_client, token_for, tid, cid)
    _finding(analytics_client, token_for, tid, cid)
    analytics_client.post(_url(tid, cid, "/conclude"), headers=token_for("risk_manager"),
                          json={"conclusion": "Concluded."})
    r = analytics_client.post(f"/analytics/{tid}/cases/{cid}/transition",
                              headers=token_for("risk_manager"),
                              json={"action": "close_case", "reason": "Done."})
    assert r.status_code == 200, r.text
    assert r.json()["state"] == "closed_fraud"


def test_an_open_examination_does_not_block_the_return():
    """RBI is explicit that reporting must not wait for the accountability exercise.

    Asserted against the lifecycle definition rather than through the API, because the
    point is that no such guard exists on the filing path at all - a test that merely
    filed successfully would still pass if someone later added the guard and the
    examination happened to be concluded.
    """
    assert wf.BY_ACTION["file_fmr"].requires_accountability is False
    assert wf.BY_ACTION["close_case"].requires_accountability is True
    guarded = [t.action for t in wf.TRANSITIONS if t.requires_accountability]
    assert guarded == ["close_case"], f"accountability must gate closing only: {guarded}"


def test_declaring_fraud_starts_the_accountability_clock(analytics_client, token_for,
                                                         tid):
    """The clock is the tenant's own board-approved window, not a constant."""
    ctx = wf.Context(state="response_evaluation", role="risk_manager", actor="x",
                     now=datetime(2026, 1, 1, tzinfo=timezone.utc),
                     policy={"staff_accountability_days": 90}, doc_types=set(),
                     response_due_ts=None)
    clocks = wf.clocks_for(wf.BY_ACTION["declare_fraud"], ctx)
    assert clocks["accountability_due_ts"] == datetime(2026, 4, 1, tzinfo=timezone.utc)


# ------------------------------------------------------- the register
def test_the_register_puts_the_overdue_and_unexamined_first(
        analytics_client, token_for, tid, case):
    """The view a Special Committee asks for: what has been ignored, at the top."""
    overdue = case(state="fraud_declared", due_in_days=-30, tag="ACCOD")
    fine = case(state="fraud_declared", due_in_days=120, tag="ACCOK")
    _open(analytics_client, token_for, tid, fine)
    _finding(analytics_client, token_for, tid, fine)
    analytics_client.post(_url(tid, fine, "/conclude"), headers=token_for("risk_manager"),
                          json={"conclusion": "Concluded."})

    body = analytics_client.get(f"/analytics/{tid}/accountability/register",
                                headers=token_for("supervisor")).json()
    ids = [i["case_id"] for i in body["items"]]
    assert ids.index(overdue) < ids.index(fine), f"overdue case not surfaced first: {ids}"
    row = next(i for i in body["items"] if i["case_id"] == overdue)
    assert row["overdue"] is True and row["status"] == ""
    done = next(i for i in body["items"] if i["case_id"] == fine)
    assert done["status"] == "concluded" and done["staff_implicated"] == 1
    assert body["overdue"] >= 1 and body["concluded"] >= 1


def test_the_register_counts_reconcile_with_its_own_rows(analytics_client, token_for,
                                                         tid, case):
    """A summary that disagrees with the detail below it is worse than no summary."""
    case(state="fraud_declared", due_in_days=-5, tag="ACCR")
    body = analytics_client.get(f"/analytics/{tid}/accountability/register",
                                headers=token_for("supervisor")).json()
    items = body["items"]
    assert body["count"] == len(items)
    assert body["overdue"] == sum(1 for i in items if i["overdue"])
    assert body["not_started"] == sum(1 for i in items if not i["status"])
    assert body["concluded"] == sum(1 for i in items if i["status"] == "concluded")
    assert body["staff_implicated"] == sum(i["staff_implicated"] for i in items)


def test_the_examination_is_scoped_to_its_tenant(analytics_client, token_for, tid, case):
    cid = case()
    r = analytics_client.get(_url("00000000-0000-0000-0000-000000000000", cid),
                             headers=token_for("supervisor"))
    assert r.status_code in (403, 404)


def test_findings_are_stored_against_the_examination(analytics_client, token_for, tid,
                                                     case):
    """Guards against the child rows being orphaned from their parent."""
    cid = case()
    _open(analytics_client, token_for, tid, cid)
    _finding(analytics_client, token_for, tid, cid)
    db = SessionLocal()
    try:
        exam = db.scalars(select(StaffAccountability).where(
            StaffAccountability.case_id == cid)).one()
        rows = db.scalars(select(AccountabilityFinding).where(
            AccountabilityFinding.case_id == cid)).all()
        assert len(rows) == 1
        assert rows[0].examination_id == exam.id
        assert rows[0].tenant_id == exam.tenant_id == tid
    finally:
        db.close()
