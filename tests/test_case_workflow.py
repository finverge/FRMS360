"""The RFA lifecycle: guards, natural justice, maker-checker, and the audit trail.

These are compliance guarantees, not conveniences, so each one is tested for the failure
it is supposed to prevent rather than for the happy path alone.
"""
import pytest
from sqlalchemy import delete, select

from cp_common.db import SessionLocal
from services.analytics_service.app.accountability_model import (
    AccountabilityFinding, StaffAccountability,
)
from services.analytics_service.app.documents_model import CaseDocument
from services.analytics_service.app.models import FactCase
from services.analytics_service.app.workflow_model import CaseTransition


# --------------------------------------------------------------------------- helpers
def _fresh_case(tid: str, state: str = "under_review", **cols) -> str:
    """A case in a known state, isolated from the synthetic corpus."""
    db = SessionLocal()
    try:
        case_id = f"WF-{state}-{cols.pop('tag', '')}-{db.query(FactCase).count()}"
        seed = db.scalars(select(FactCase).where(FactCase.tenant_id == tid).limit(1)).first()
        assert seed is not None, "no seeded case to copy dimensions from"
        db.add(FactCase(
            case_id=case_id, tenant_id=tid, opened_ts=seed.opened_ts, state=state,
            severity="high", fmr_category="cheating_and_forgery", amount_paise=5_000_00,
            recovered_paise=0, rfa_flag=state != "under_review", rail="UPI",
            region="south", product="savings", customer_segment="retail",
            assignee="", **cols))
        db.commit()
        return case_id
    finally:
        db.close()


def _cleanup(case_id: str) -> None:
    db = SessionLocal()
    try:
        db.execute(delete(AccountabilityFinding).where(
            AccountabilityFinding.case_id == case_id))
        db.execute(delete(StaffAccountability).where(
            StaffAccountability.case_id == case_id))
        db.execute(delete(CaseTransition).where(CaseTransition.case_id == case_id))
        db.execute(delete(CaseDocument).where(CaseDocument.case_id == case_id))
        db.execute(delete(FactCase).where(FactCase.case_id == case_id))
        db.commit()
    finally:
        db.close()


@pytest.fixture
def case(tid):
    made = []

    def _make(state="under_review", **cols):
        cid = _fresh_case(tid, state, **cols)
        made.append(cid)
        return cid

    yield _make
    for cid in made:
        _cleanup(cid)


def _move(client, tid, case_id, headers, action, reason="because"):
    return client.post(f"/analytics/{tid}/cases/{case_id}/transition",
                       headers=headers, json={"action": action, "reason": reason})


def _attach_reasoned_order(tid, case_id):
    db = SessionLocal()
    try:
        db.add(CaseDocument(tenant_id=tid, case_id=case_id, doc_type="reasoned_order",
                            filename="order.pdf", content_type="application/pdf",
                            size_bytes=10, sha256="x" * 64, uploaded_by="tester",
                            content=b"reasoned order"))
        db.commit()
    finally:
        db.close()


# --------------------------------------------------------------------- the happy path
def test_full_lifecycle_reaches_closed_fraud(analytics_client, token_for, tid, case):
    """under_review -> ... -> closed_fraud, with every guard satisfied legitimately."""
    cid = case("under_review")
    inv, rm, sup = token_for("investigator"), token_for("risk_manager"), token_for("supervisor")

    assert _move(analytics_client, tid, cid, inv, "flag_rfa").status_code == 200
    assert _move(analytics_client, tid, cid, inv, "issue_show_cause").status_code == 200
    assert _move(analytics_client, tid, cid, inv, "record_response").status_code == 200

    _attach_reasoned_order(tid, cid)
    # Maker proposes...
    r = _move(analytics_client, tid, cid, inv, "declare_fraud")
    assert r.json()["outcome"] == "proposed", r.text
    # ...checker approves.
    r = _move(analytics_client, tid, cid, rm, "declare_fraud")
    assert r.json()["outcome"] == "applied", r.text
    assert r.json()["state"] == "fraud_declared"

    # The return goes out with the staff accountability exercise still untouched. That
    # ordering is the requirement, not an accident of this test: RBI is explicit that
    # reporting must not wait for the accountability examination (BR-414).
    assert _move(analytics_client, tid, cid, sup, "file_fmr").status_code == 200

    # Closing, however, may not happen with the question unanswered.
    blocked = _move(analytics_client, tid, cid, sup, "close_case")
    assert blocked.status_code == 409
    assert blocked.json()["error"]["code"] == "accountability_pending"

    acc = f"/analytics/{tid}/cases/{cid}/accountability"
    assert analytics_client.post(acc + "/open", json={}, headers=rm).status_code == 200
    assert analytics_client.post(acc + "/findings", headers=rm, json={
        "staff_ref": "EMP-77", "staff_name": "R. Iyer", "finding": "no_lapse",
        "action_taken": "none"}).status_code == 200
    assert analytics_client.post(acc + "/conclude", headers=rm, json={
        "conclusion": "No staff lapse established."}).status_code == 200

    r = _move(analytics_client, tid, cid, sup, "close_case")
    assert r.json()["state"] == "closed_fraud"


# ------------------------------------------------------------------- natural justice
def test_the_response_window_cannot_be_cut_short(analytics_client, token_for, tid, case):
    """The core protection from SBI v. Rajesh Agarwal.

    A case in natural_justice with a live response window must not be pushed onward,
    however senior the user is.
    """
    cid = case("natural_justice")
    _move(analytics_client, tid, cid, token_for("investigator"), "issue_show_cause")
    # issue_show_cause is not valid from natural_justice; set the window via the real path
    cid2 = case("rfa_flagged")
    _move(analytics_client, tid, cid2, token_for("investigator"), "issue_show_cause")

    r = _move(analytics_client, tid, cid2, token_for("tenant_admin"), "close_window")
    assert r.status_code == 409, r.text
    assert r.json()["error"]["code"] == "window_open"
    assert "may not be cut short" in r.json()["error"]["message"]


def test_a_borrower_response_may_arrive_at_any_time(analytics_client, token_for, tid, case):
    """Recording a reply is not the same as skipping the window - it must stay open."""
    cid = case("rfa_flagged")
    _move(analytics_client, tid, cid, token_for("investigator"), "issue_show_cause")
    r = _move(analytics_client, tid, cid, token_for("investigator"), "record_response")
    assert r.status_code == 200
    assert r.json()["state"] == "response_evaluation"


def test_the_window_uses_the_tenants_own_policy(analytics_client, token_for, tid, case):
    cid = case("rfa_flagged")
    _move(analytics_client, tid, cid, token_for("investigator"), "issue_show_cause")
    wf = analytics_client.get(f"/analytics/{tid}/cases/{cid}/workflow",
                              headers=token_for("investigator")).json()
    assert wf["clocks"]["response_due_ts"], "no response window was set"
    assert wf["policy"]["natural_justice_days"], "policy window not surfaced"


# --------------------------------------------------------------------- reasoned order
def test_fraud_cannot_be_declared_without_a_reasoned_order(analytics_client, token_for, tid, case):
    """The condition the fraud_without_reasoned_order metric reports on, now prevented."""
    cid = case("response_evaluation")
    r = _move(analytics_client, tid, cid, token_for("risk_manager"), "declare_fraud")
    assert r.status_code == 409, r.text
    assert r.json()["error"]["code"] == "document_required"


# ---------------------------------------------------------------------- maker-checker
def test_one_person_cannot_declare_fraud_alone(analytics_client, token_for, tid, case):
    cid = case("response_evaluation")
    _attach_reasoned_order(tid, cid)
    rm = token_for("risk_manager")

    first = _move(analytics_client, tid, cid, rm, "declare_fraud")
    assert first.json()["outcome"] == "proposed"

    second = _move(analytics_client, tid, cid, rm, "declare_fraud")
    assert second.status_code == 409, second.text
    assert second.json()["error"]["code"] == "self_approval"

    db = SessionLocal()
    try:
        state = db.get(FactCase, cid).state
    finally:
        db.close()
    assert state == "response_evaluation", "the case moved on a single person's say-so"


def test_the_checker_must_hold_an_approver_role(analytics_client, token_for, tid, case):
    cid = case("response_evaluation")
    _attach_reasoned_order(tid, cid)
    _move(analytics_client, tid, cid, token_for("investigator"), "declare_fraud")
    # A second investigator is still not an approver.
    r = _move(analytics_client, tid, cid, token_for("supervisor"), "declare_fraud")
    assert r.status_code == 409
    assert r.json()["error"]["code"] in ("role_not_permitted", "not_an_approver")


# ------------------------------------------------------------------------------ rbac
def test_read_only_roles_cannot_move_a_case(analytics_client, token_for, tid, case):
    cid = case("under_review")
    for role in ("board", "rbi_inspector", "data_scientist"):
        r = _move(analytics_client, tid, cid, token_for(role), "flag_rfa")
        assert r.status_code == 409, f"{role} was allowed to act"
        assert r.json()["error"]["code"] == "role_not_permitted"


def test_an_action_from_the_wrong_state_is_refused(analytics_client, token_for, tid, case):
    cid = case("under_review")
    r = _move(analytics_client, tid, cid, token_for("supervisor"), "file_fmr")
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "wrong_state"


def test_a_reason_is_required_where_the_lifecycle_demands_one(analytics_client, token_for, tid, case):
    cid = case("under_review")
    r = _move(analytics_client, tid, cid, token_for("investigator"), "flag_rfa", reason="  ")
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "reason_required"


# --------------------------------------------------------------------------- the log
def test_every_attempt_is_recorded_including_refusals(analytics_client, token_for, tid, case):
    """A pattern of trying to skip a window must not be invisible to an inspection."""
    cid = case("rfa_flagged")
    _move(analytics_client, tid, cid, token_for("investigator"), "issue_show_cause")
    _move(analytics_client, tid, cid, token_for("tenant_admin"), "close_window")  # refused

    h = analytics_client.get(f"/analytics/{tid}/cases/{cid}/history",
                             headers=token_for("investigator")).json()["rows"]
    statuses = [(r["action"], r["status"], r["refusal_code"]) for r in h]
    assert ("issue_show_cause", "applied", "") in statuses
    assert ("close_window", "refused", "window_open") in statuses


def test_the_maker_and_the_checker_are_both_provable(analytics_client, token_for, tid, case):
    cid = case("response_evaluation")
    _attach_reasoned_order(tid, cid)
    _move(analytics_client, tid, cid, token_for("investigator"), "declare_fraud")
    _move(analytics_client, tid, cid, token_for("risk_manager"), "declare_fraud")

    rows = analytics_client.get(f"/analytics/{tid}/cases/{cid}/history",
                                headers=token_for("investigator")).json()["rows"]
    proposed = next(r for r in rows if r["status"] == "approved")
    applied = next(r for r in rows if r["status"] == "applied")
    assert applied["approves_id"] == proposed["id"], "approval is not linked to its proposal"
    assert applied["actor"] != proposed["actor"], "maker and checker are the same person"


def test_case_state_is_never_writable_except_through_the_lifecycle(analytics_client, token_for, tid):
    """There must be no other route that sets state - the guards would be decorative."""
    import pathlib
    import re
    root = pathlib.Path(__file__).resolve().parents[1] / "services"
    offenders = []
    for path in root.rglob("*.py"):
        if path.name in ("cases.py", "models.py"):
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            if re.search(r"\.state\s*=\s*", line) and "case" in line.lower():
                offenders.append(f"{path.name}: {line.strip()}")
    assert not offenders, f"case.state written outside the workflow: {offenders}"


# ------------------------------------------------------------------------ assignment
def test_assignment_is_recorded_and_role_gated(analytics_client, token_for, tid, case):
    cid = case("under_review")
    ok = analytics_client.post(f"/analytics/{tid}/cases/{cid}/assign",
                               headers=token_for("risk_manager"),
                               json={"assignee": "asha.n"})
    assert ok.status_code == 200 and ok.json()["assignee"] == "asha.n"
    denied = analytics_client.post(f"/analytics/{tid}/cases/{cid}/assign",
                                   headers=token_for("board"), json={"assignee": "x"})
    assert denied.status_code == 403


# -------------------------------------------------------------------------- tenancy
def test_workflow_is_tenant_scoped(analytics_client, token_for, tid, case):
    cid = case("under_review")
    r = analytics_client.get(f"/analytics/some-other-tenant/cases/{cid}/workflow",
                             headers=token_for("investigator"))
    assert r.status_code == 403


def test_available_actions_explain_why_they_are_blocked(analytics_client, token_for, tid, case):
    """A disabled control with a reason teaches the process; a hidden one looks broken."""
    cid = case("rfa_flagged")
    _move(analytics_client, tid, cid, token_for("investigator"), "issue_show_cause")
    wf = analytics_client.get(f"/analytics/{tid}/cases/{cid}/workflow",
                              headers=token_for("investigator")).json()
    blocked = {a["action"]: a for a in wf["actions"] if not a["allowed"]}
    assert "close_window" in blocked
    assert blocked["close_window"]["blocked_code"] == "window_open"
    assert blocked["close_window"]["blocked_reason"], "no explanation given to the user"


# ------------------------------------------------------------------- concurrency
def test_a_retried_approval_cannot_apply_twice(analytics_client, token_for, tid, case):
    """Behind a load balancer a client retry, or an impatient double-click, arrives as
    two identical requests. The second must find the case already moved on, not apply
    the transition a second time."""
    cid = case("response_evaluation")
    _attach_reasoned_order(tid, cid)
    _move(analytics_client, tid, cid, token_for("investigator"), "declare_fraud")
    first = _move(analytics_client, tid, cid, token_for("risk_manager"), "declare_fraud")
    assert first.json()["outcome"] == "applied"

    replay = _move(analytics_client, tid, cid, token_for("risk_manager"), "declare_fraud")
    assert replay.status_code == 409
    assert replay.json()["error"]["code"] == "wrong_state"

    rows = analytics_client.get(f"/analytics/{tid}/cases/{cid}/history",
                                headers=token_for("investigator")).json()["rows"]
    applied = [r for r in rows if r["status"] == "applied" and r["action"] == "declare_fraud"]
    assert len(applied) == 1, f"declare_fraud applied {len(applied)} times"


def test_simultaneous_transitions_serialise_to_one_winner(analytics_client, token_for, tid, case):
    """Two replicas acting on the same case at the same instant.

    Without SELECT ... FOR UPDATE both requests read 'under_review', both pass the guard
    and both write - so the row lock is what makes the state machine true under load,
    not the Python checks above it.
    """
    import concurrent.futures as cf
    cid = case("under_review")
    inv, rm = token_for("investigator"), token_for("risk_manager")

    with cf.ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(_move, analytics_client, tid, cid, h, "flag_rfa")
                   for h in (inv, rm)]
        codes = [f.result().status_code for f in futures]

    assert sorted(codes) == [200, 409], f"expected exactly one winner, got {codes}"
    rows = analytics_client.get(f"/analytics/{tid}/cases/{cid}/history",
                                headers=inv).json()["rows"]
    applied = [r for r in rows if r["status"] == "applied"]
    assert len(applied) == 1, f"{len(applied)} transitions applied concurrently"


# ------------------------------------------------------------------------ the UI
def _app_js() -> str:
    import pathlib
    return (pathlib.Path(__file__).resolve().parents[1]
            / "services/gateway/app/static/app.js").read_text(encoding="utf-8")


def test_the_console_renders_every_state_the_server_can_report():
    """Add a state to the lifecycle and forget the console, and the stepper silently
    shows no current step - the case looks like it is nowhere."""
    import re

    from services.analytics_service.app.models import CASE_STATES

    js = _app_js()
    track = re.search(r"const WF_TRACK = \[(.*?)\];", js, re.S)
    assert track, "WF_TRACK not found in the console"
    rendered = set(re.findall(r'\["(\w+)",', track.group(1)))
    # exonerated is a terminal branch drawn separately rather than a step on the track.
    rendered.add("exonerated")
    missing = sorted(set(CASE_STATES) - rendered)
    assert not missing, f"the console cannot render these case states: {missing}"


def test_the_console_offers_every_action_the_server_defines():
    """Actions are rendered from the server's response, so nothing needs hardcoding -
    but the styling maps must not name an action that no longer exists."""
    import re

    from services.analytics_service.app.workflow import BY_ACTION

    js = _app_js()
    for const in ("WF_PRIMARY", "WF_GRAVE"):
        block = re.search(const + r" = new Set\(\[(.*?)\]\)", js, re.S)
        assert block, f"{const} not found"
        named = set(re.findall(r'"(\w+)"', block.group(1)))
        unknown = sorted(named - set(BY_ACTION))
        assert not unknown, f"{const} names actions the lifecycle does not define: {unknown}"


def test_blocked_actions_are_shown_with_their_reason_not_hidden():
    """A disabled control that explains itself teaches the process; a missing one reads
    as a broken page. The API returns blocked_reason precisely so the UI can say it."""
    js = _app_js()
    actions = js.split("function wfActions(", 1)[1].split("\nfunction ", 1)[0]
    assert "blocked_reason" in actions, "the console discards the server's explanation"
    assert "disabled" in actions, "blocked actions are not disabled"
    assert "hidden" not in actions, "blocked actions appear to be hidden rather than disabled"
