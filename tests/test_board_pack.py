"""The board / ACB pack (BR-507).

What matters here is not that a document is produced. It is that the pack agrees with the
dashboards the executives have already seen, that it reports the position of a *finished*
period rather than the one still running, that what the committee was told can be
reproduced years later, and that "circulated to the board" is backed by named recipients
rather than asserted.
"""
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select, text

from cp_common.db import SessionLocal
from services.analytics_service.app.board_pack import builder, renderers
from services.analytics_service.app.board_pack.sections import SECTIONS
from services.analytics_service.app.board_pack_model import (
    BoardPack, BoardPackDistribution,
)


@pytest.fixture(autouse=True)
def _clean(tid):
    yield
    db = SessionLocal()
    try:
        db.execute(text("DELETE FROM analytics.board_pack_distributions "
                        "WHERE tenant_id = :t"), {"t": tid})
        db.execute(text("DELETE FROM analytics.board_packs WHERE tenant_id = :t"),
                   {"t": tid})
        db.commit()
    finally:
        db.close()


def _gen(client, token_for, tid, role="risk_manager", **body):
    return client.post(f"/analytics/{tid}/board-packs/generate",
                       headers=token_for(role), json=body)


# ------------------------------------------------------------- the period maths
@pytest.mark.parametrize("when,cadence,label,start,end", [
    # Indian financial year: Q1 is April-June, not January-March.
    (datetime(2026, 5, 9, tzinfo=timezone.utc), "quarterly", "Q1 FY2026-27",
     datetime(2026, 4, 1, tzinfo=timezone.utc), datetime(2026, 7, 1, tzinfo=timezone.utc)),
    (datetime(2027, 2, 9, tzinfo=timezone.utc), "quarterly", "Q4 FY2026-27",
     datetime(2027, 1, 1, tzinfo=timezone.utc), datetime(2027, 4, 1, tzinfo=timezone.utc)),
    (datetime(2026, 8, 3, tzinfo=timezone.utc), "half_yearly", "H1 FY2026-27",
     datetime(2026, 4, 1, tzinfo=timezone.utc), datetime(2026, 10, 1, tzinfo=timezone.utc)),
    (datetime(2027, 2, 3, tzinfo=timezone.utc), "annual", "FY2026-27",
     datetime(2026, 4, 1, tzinfo=timezone.utc), datetime(2027, 4, 1, tzinfo=timezone.utc)),
])
def test_periods_follow_the_indian_financial_year(when, cadence, label, start, end):
    s, e, lab = builder.period_for(when, cadence)
    assert (lab, s, e) == (label, start, end)


def test_consecutive_periods_abut_exactly(tid):
    """No gap for a transaction to fall into, no overlap to count it twice.

    Every engine filter is ``ts < date_to``, so the end must be exclusive. An inclusive
    23:59:59 end silently drops the last second of the quarter.
    """
    cursor = datetime(2026, 4, 1, tzinfo=timezone.utc)
    prev_end = None
    for _ in range(12):
        s, e, _lab = builder.period_for(cursor, "quarterly")
        if prev_end is not None:
            assert s == prev_end, f"period boundary is not contiguous: {prev_end} -> {s}"
        prev_end, cursor = e, e + timedelta(days=1)


# ------------------------------------------------------------- generation
def test_generating_defaults_to_the_last_completed_period(analytics_client, token_for,
                                                          tid):
    """A committee reviewing the quarter it is sitting in reads a partial number as final."""
    r = _gen(analytics_client, token_for, tid)
    assert r.status_code == 200, r.text
    body = r.json()
    now = datetime.now(timezone.utc)
    current_start, _, _ = builder.period_for(now, body["cadence"])
    assert datetime.fromisoformat(body["period_end"].replace("Z", "+00:00")) <= current_start


def test_the_pack_carries_every_section_even_when_empty(analytics_client, token_for, tid):
    """"No frauds above the floor" is a governance statement, not a reason to omit."""
    pid = _gen(analytics_client, token_for, tid).json()["id"]
    payload = analytics_client.get(f"/analytics/{tid}/board-packs/{pid}",
                                   headers=token_for("risk_manager")).json()["payload"]
    keys = [s["key"] for s in payload["sections"]]
    assert keys == [s.key for s in SECTIONS], f"sections missing from the pack: {keys}"
    assert all(s.get("why") for s in payload["sections"]), "a section has no rationale"


def test_a_pack_is_hashed_and_versioned(analytics_client, token_for, tid):
    body = _gen(analytics_client, token_for, tid).json()
    assert len(body["content_hash"]) == 64
    assert body["revision"] == 1
    assert body["status"] == "draft"


def test_regenerating_supersedes_the_earlier_draft(analytics_client, token_for, tid):
    first = _gen(analytics_client, token_for, tid).json()
    second = _gen(analytics_client, token_for, tid).json()
    assert second["revision"] == 2
    packs = {p["id"]: p for p in analytics_client.get(
        f"/analytics/{tid}/board-packs", headers=token_for("risk_manager")).json()}
    assert packs[first["id"]]["status"] == "superseded"
    assert packs[second["id"]]["status"] == "draft"


def test_an_unknown_cadence_is_refused(analytics_client, token_for, tid):
    r = _gen(analytics_client, token_for, tid, cadence="fortnightly")
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "unknown_cadence"


# ------------------------------------------------------------- reconciliation
def test_the_pack_agrees_with_the_board_dashboard(analytics_client, token_for, tid):
    """The property the whole feature rests on.

    The pack reads the same metric registry the dashboards read. If it ever recomputed
    its own numbers they would drift, and the reconciliation would happen in the meeting.
    """
    # The *current* period, because that is where the seeded dataset sits. Run against
    # the default (last completed) period this compares zero to zero and would pass with
    # the reconciliation completely broken.
    body = _gen(analytics_client, token_for, tid,
                as_of=datetime.now(timezone.utc).isoformat()).json()
    pid, start, end = body["id"], body["period_start"], body["period_end"]
    payload = analytics_client.get(f"/analytics/{tid}/board-packs/{pid}",
                                   headers=token_for("risk_manager")).json()["payload"]
    pack_metrics = {m["name"]: m["value"]
                    for s in payload["sections"] for m in s.get("metrics", [])}

    live = analytics_client.get(
        f"/analytics/{tid}/board", headers=token_for("risk_manager"),
        params={"date_from": start, "date_to": end}).json()["metrics"]

    compared = nonzero = 0
    for name, m in live.items():
        if name in pack_metrics and not m.get("volatile"):
            assert pack_metrics[name] == m["value"], (
                f"{name}: pack says {pack_metrics[name]}, dashboard says {m['value']}")
            compared += 1
            nonzero += 1 if m["value"] else 0
    assert compared >= 5, f"too few metrics actually compared ({compared})"
    assert nonzero >= 2, (
        "every compared figure was zero - this proves nothing about reconciliation")


def test_a_breakdown_sums_to_its_own_metric(analytics_client, token_for, tid):
    """Invariant R-06/R-07 restated for the pack: groups must partition the total."""
    pid = _gen(analytics_client, token_for, tid,
               as_of=datetime.now(timezone.utc).isoformat()).json()["id"]
    payload = analytics_client.get(f"/analytics/{tid}/board-packs/{pid}",
                                   headers=token_for("risk_manager")).json()["payload"]
    totals = {m["name"]: m["value"]
              for s in payload["sections"] for m in s.get("metrics", [])}
    checked = nonzero = 0
    for s in payload["sections"]:
        for b in s.get("breakdowns", []):
            if not b.get("available") or b["metric"] not in totals:
                continue
            grouped = sum(r["value"] for r in b["rows"])
            total = totals[b["metric"]]
            # Exact, not approximate. These are integer paise: a relative tolerance of
            # 1e-6 against a ₹67 crore total silently permits a drift of hundreds of
            # rupees, and a partition that is nearly right is a partition that is wrong.
            if isinstance(total, int) and isinstance(grouped, int):
                assert grouped == total, (
                    f"{b['metric']} by {b['dimension']}: groups sum to {grouped}, "
                    f"total is {total}")
            else:
                assert grouped == pytest.approx(total, rel=1e-9), (
                    f"{b['metric']} by {b['dimension']}: groups sum to {grouped}, "
                    f"total is {total}")
            checked += 1
            nonzero += 1 if totals[b["metric"]] else 0
    assert checked >= 1, "no breakdown was actually reconciled"
    assert nonzero >= 1, "every reconciled breakdown was empty - this proves nothing"


# ------------------------------------------------------------- issuing
def test_a_pack_cannot_be_issued_to_nobody(analytics_client, token_for, tid):
    """A pack issued to no one evidences nothing."""
    pid = _gen(analytics_client, token_for, tid).json()["id"]
    r = analytics_client.post(f"/analytics/{tid}/board-packs/{pid}/issue",
                              headers=token_for("risk_manager"),
                              json={"recipients": [], "note": "Reviewed."})
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "no_recipients"


def test_issuing_records_each_recipient_individually(analytics_client, token_for, tid):
    """"Circulated to the ACB" is an assertion; a row per recipient is evidence."""
    pid = _gen(analytics_client, token_for, tid).json()["id"]
    r = analytics_client.post(
        f"/analytics/{tid}/board-packs/{pid}/issue", headers=token_for("risk_manager"),
        json={"recipients": ["chair.acb@bank.example.com", "cro@bank.example.com"],
              "note": "Noted by the committee."})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "issued"
    assert {d["recipient"] for d in body["distributions"]} == {
        "chair.acb@bank.example.com", "cro@bank.example.com"}
    # Delivery goes through the notification service, which owns channels, preferences
    # and the retry ledger. Each row records what actually happened - never an
    # assumption that it worked.
    for d in body["distributions"]:
        assert d["status"] in ("sent", "failed"), d
        assert d["detail"], "a distribution row records no outcome"


def test_a_notification_reaches_each_recipient(analytics_client, notification_client,
                                               token_for, tid):
    """"Circulated to the committee" has to mean someone was actually told."""
    pid = _gen(analytics_client, token_for, tid).json()["id"]
    who = "chair.acb@bank.example.com"
    analytics_client.post(f"/analytics/{tid}/board-packs/{pid}/issue",
                          headers=token_for("risk_manager"),
                          json={"recipients": [who], "note": "Q review."})

    db = SessionLocal()
    try:
        rows = db.execute(text(
            "SELECT kind, recipient, subject FROM notify.notifications "
            "WHERE tenant_id = :t AND recipient = :r AND kind = 'board_pack_issued'"),
            {"t": tid, "r": who}).all()
    finally:
        db.close()
    assert rows, "the recipient was recorded but never notified"
    assert "pack" in rows[0][2].lower()


def test_a_failed_notification_is_recorded_not_swallowed(analytics_client, token_for,
                                                         tid, monkeypatch):
    """The pack is already frozen, so a relay outage must not unwind it - but the row
    must not claim a delivery that did not happen."""
    import services.analytics_service.app.routes.board_pack as bp

    def boom(*a, **kw):
        raise RuntimeError("relay unreachable")

    monkeypatch.setattr(bp.httpx, "post", boom)
    pid = _gen(analytics_client, token_for, tid).json()["id"]
    body = analytics_client.post(
        f"/analytics/{tid}/board-packs/{pid}/issue", headers=token_for("risk_manager"),
        json={"recipients": ["nobody@bank.example.com"]}).json()
    assert body["status"] == "issued", "the pack was unwound by a delivery failure"
    d = body["distributions"][0]
    assert d["status"] == "failed"
    assert "has not been told" in d["detail"]


def test_an_issued_pack_cannot_be_issued_again(analytics_client, token_for, tid):
    pid = _gen(analytics_client, token_for, tid).json()["id"]
    body = {"recipients": ["chair.acb@bank.example.com"]}
    assert analytics_client.post(f"/analytics/{tid}/board-packs/{pid}/issue",
                                 headers=token_for("risk_manager"),
                                 json=body).status_code == 200
    r = analytics_client.post(f"/analytics/{tid}/board-packs/{pid}/issue",
                              headers=token_for("risk_manager"), json=body)
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "already_issued"


def test_regenerating_does_not_supersede_an_issued_pack(analytics_client, token_for, tid):
    """The committee has seen it and the minutes refer to it."""
    pid = _gen(analytics_client, token_for, tid).json()["id"]
    analytics_client.post(f"/analytics/{tid}/board-packs/{pid}/issue",
                          headers=token_for("risk_manager"),
                          json={"recipients": ["chair.acb@bank.example.com"]})
    _gen(analytics_client, token_for, tid)
    packs = {p["id"]: p for p in analytics_client.get(
        f"/analytics/{tid}/board-packs", headers=token_for("risk_manager")).json()}
    assert packs[pid]["status"] == "issued", "an issued pack was superseded"


def test_a_supervisor_may_prepare_but_not_issue(analytics_client, token_for, tid):
    r = _gen(analytics_client, token_for, tid, role="supervisor")
    assert r.status_code == 200, r.text
    pid = r.json()["id"]
    bad = analytics_client.post(f"/analytics/{tid}/board-packs/{pid}/issue",
                                headers=token_for("supervisor"),
                                json={"recipients": ["chair.acb@bank.example.com"]})
    assert bad.status_code == 403


def test_an_analyst_may_not_prepare_a_pack(analytics_client, token_for, tid):
    assert _gen(analytics_client, token_for, tid, role="analyst").status_code == 403


# ------------------------------------------------------------- reproducibility
def test_the_download_renders_the_stored_payload_not_a_fresh_query(
        analytics_client, token_for, tid):
    """Opening last year's pack must show what the committee saw, not today's numbers."""
    pid = _gen(analytics_client, token_for, tid).json()["id"]

    # Rewrite the stored payload to something unmistakable. If the renderer re-queried,
    # this value could not possibly appear.
    db = SessionLocal()
    try:
        row = db.scalars(select(BoardPack).where(BoardPack.id == pid)).one()
        payload = dict(row.payload)
        payload["sections"] = [dict(payload["sections"][0])]
        payload["sections"][0]["metrics"] = [{
            "name": "fraud_value_total", "label": "Total fraud value",
            "value": 424242, "unit": "inr_paise", "available": True, "volatile": False}]
        row.payload = payload
        db.commit()
    finally:
        db.close()

    html = analytics_client.get(f"/analytics/{tid}/board-packs/{pid}/download",
                                headers=token_for("risk_manager")).text
    assert "4,242.42" in html, "the pack was re-queried instead of read from the record"


def test_the_html_pack_states_the_period_and_its_basis(analytics_client, token_for, tid):
    pid = _gen(analytics_client, token_for, tid).json()["id"]
    html = analytics_client.get(f"/analytics/{tid}/board-packs/{pid}/download",
                                headers=token_for("risk_manager")).text
    assert "Board / Audit Committee Pack" in html
    assert "case opening date" in html, "the pack does not state what 'for the period' means"
    assert renderers.DISCLAIMER[:40] in html


def test_the_schedule_declares_who_may_prepare_and_issue(analytics_client, token_for,
                                                         tid):
    """So the console disables the control with its reason rather than failing on click."""
    sup = analytics_client.get(f"/analytics/{tid}/board-packs/schedule/due",
                               headers=token_for("supervisor")).json()
    assert sup["may_prepare"] is True
    assert sup["may_issue"] is False
    assert "Fraud Risk Manager" in sup["issue_requires"]

    rm = analytics_client.get(f"/analytics/{tid}/board-packs/schedule/due",
                              headers=token_for("risk_manager")).json()
    assert rm["may_issue"] is True

    an = analytics_client.get(f"/analytics/{tid}/board-packs/schedule/due",
                              headers=token_for("analyst")).json()
    assert an["may_prepare"] is False


def test_money_is_rendered_as_rupees_not_raw_paise(analytics_client, token_for, tid):
    """The registry's unit is ``inr_paise``, not ``paise``.

    Matching the wrong string printed ₹235 crore as a bare "2,350,250,027" in a document
    a board reads. Nothing failed; the number was simply wrong by a factor of a hundred
    and carried no currency at all.
    """
    payload = {"entity": {}, "period": {"label": "Q1 FY2026-27", "start": "2026-04-01",
                                        "end_inclusive": "2026-06-30", "basis": "x"},
               "policy": {}, "sections": [{
                   "key": "position", "title": "Fraud position", "why": "w",
                   "metrics": [{"name": "fraud_value_total", "label": "Total fraud value",
                                "value": 2350250027, "unit": "inr_paise",
                                "available": True}],
                   "breakdowns": []}]}
    html = renderers.to_html(payload)
    assert "₹23,502,500.27" in html, "money was not rendered as rupees"
    assert ">2,350,250,027<" not in html, "raw paise leaked into the pack"


def test_the_materiality_table_lists_only_actual_frauds(analytics_client, token_for, tid):
    """An exonerated allegation must not appear under a fraud heading.

    Filtering on amount alone put cases that were investigated and *not sustained* into
    the table the board reads as its loss list.
    """
    pid = _gen(analytics_client, token_for, tid,
               as_of=datetime.now(timezone.utc).isoformat()).json()["id"]
    payload = analytics_client.get(f"/analytics/{tid}/board-packs/{pid}",
                                   headers=token_for("risk_manager")).json()["payload"]
    mat = next(s for s in payload["sections"] if s["key"] == "materiality")
    states = {c["state"] for c in mat["cases"]}
    assert states <= {"fraud_declared", "fmr_reported", "closed_fraud"}, (
        f"non-fraud states presented to the board as frauds: {states}")


def test_a_pack_is_scoped_to_its_tenant(analytics_client, token_for, tid):
    pid = _gen(analytics_client, token_for, tid).json()["id"]
    r = analytics_client.get(
        f"/analytics/00000000-0000-0000-0000-000000000000/board-packs/{pid}",
        headers=token_for("risk_manager"))
    assert r.status_code in (403, 404)


# ------------------------------------------------------------- the schedule
def test_the_schedule_reports_periods_that_went_by_with_no_pack(analytics_client,
                                                                token_for, tid):
    """The governance gap: not "is one overdue" but "which periods got nothing"."""
    d = analytics_client.get(f"/analytics/{tid}/board-packs/schedule/due",
                             headers=token_for("risk_manager")).json()
    assert d["count"] > 0
    assert d["missing"] == sum(1 for p in d["periods"] if p["status"] == "missing")
    before = d["missing"]

    body = _gen(analytics_client, token_for, tid).json()
    after = analytics_client.get(f"/analytics/{tid}/board-packs/schedule/due",
                                 headers=token_for("risk_manager")).json()
    assert after["missing"] == before - 1, "generating a pack did not close the gap"
    filled = next(p for p in after["periods"]
                  if p["period_label"] == body["period_label"])
    assert filled["status"] == "draft" and filled["pack_id"] == body["id"]
