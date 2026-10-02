"""LNC-02/LNC-22 - project scope creep and cost variance against a sanctioned
project-appraisal baseline. Pure-math tests for both signal functions, plus a real
API round-trip proving submission -> runner lookup -> signal actually works.
"""
from datetime import date

import pytest
from sqlalchemy import text

from cp_common.db import SessionLocal
from services.lane_c_service.app.runner import (
    _project_baseline, _project_progress, _project_progress_history,
)
from services.lane_c_service.app.signals import project_appraisal_check as pac

ACC = "AC-LNC02-BR214"


@pytest.fixture(autouse=True)
def _clean(tid):
    yield
    db = SessionLocal()
    try:
        db.execute(text("DELETE FROM lane_c.project_progress WHERE tenant_id = :t"),
                   {"t": tid})
        db.execute(text("DELETE FROM lane_c.project_appraisals WHERE tenant_id = :t"),
                   {"t": tid})
        db.commit()
    finally:
        db.close()


BASE = {"sanctioned_cost_paise": 100_000_000, "sanctioned_completion_date": date(2026, 6, 30)}


# ------------------------------------------------------------------ pure signal functions
def test_no_baseline_or_progress_is_unmeasurable():
    assert pac.compute_scope_creep(None, None).status == "unmeasurable"
    assert pac.compute_cost_variance(None, {"actual_cost_incurred_paise": 1}).status == "unmeasurable"
    assert pac.compute_scope_creep(BASE, None).status == "unmeasurable"
    assert pac.compute_cost_variance(None, None).status == "unmeasurable"


def test_no_revision_reported_passes():
    r = pac.compute_scope_creep(BASE, {"revised_completion_date": None})
    assert r.status == "pass"
    assert r.signal_code == "LNC-02"


def test_a_small_slip_passes():
    r = pac.compute_scope_creep(BASE, {"revised_completion_date": date(2026, 7, 15)})
    assert r.status == "pass"


def test_a_wide_slip_fires():
    r = pac.compute_scope_creep(BASE, {"revised_completion_date": date(2026, 11, 1)})
    assert r.status != "pass"
    assert r.status != "unmeasurable"
    assert "slip" in r.evidence.lower()


def test_cost_within_range_passes():
    r = pac.compute_cost_variance(BASE, {"actual_cost_incurred_paise": 110_000_000})
    assert r.status == "pass"
    assert r.signal_code == "LNC-22"


def test_wide_cost_variance_fires():
    r = pac.compute_cost_variance(BASE, {"actual_cost_incurred_paise": 160_000_000})
    assert r.status != "pass"
    assert r.status != "unmeasurable"
    assert "variance" in r.evidence.lower()


def test_severe_cost_overrun_is_critical():
    r = pac.compute_cost_variance(BASE, {"actual_cost_incurred_paise": 200_000_000})
    assert r.status == "critical"


# ------------------------------------------------------------------ LNC-02 revision count
def test_no_history_behaves_exactly_as_before():
    """progress_history defaults to None - the pre-existing single-period behaviour
    must be unchanged for every caller that doesn't pass it."""
    r = pac.compute_scope_creep(BASE, {"revised_completion_date": date(2026, 7, 15)})
    assert r.status == "pass"


def test_a_single_revision_is_not_frequent():
    history = [{"reporting_date": date(2026, 3, 31),
               "revised_completion_date": date(2026, 7, 15)}]
    r = pac.compute_scope_creep(
        BASE, {"revised_completion_date": None}, history)
    assert r.status == "pass"


def test_two_distinct_revisions_fire_on_frequency_alone():
    """Each period's own slip is small, but the completion date has moved twice -
    RBI's 'frequent', which a single period's magnitude can never see."""
    history = [
        {"reporting_date": date(2026, 3, 31), "revised_completion_date": date(2026, 7, 10)},
        {"reporting_date": date(2026, 6, 30), "revised_completion_date": date(2026, 7, 25)},
    ]
    r = pac.compute_scope_creep(BASE, {"revised_completion_date": None}, history)
    assert r.status != "pass"
    assert r.status != "unmeasurable"
    assert "frequent" in r.evidence.lower()


def test_a_revision_back_to_the_original_date_still_counts():
    """'Frequent change' is about the date moving, not about which direction - a
    revision and then a reversion is still two changes, not a net-zero no-op."""
    history = [
        {"reporting_date": date(2026, 3, 31), "revised_completion_date": date(2026, 9, 1)},
        {"reporting_date": date(2026, 6, 30),
         "revised_completion_date": BASE["sanctioned_completion_date"]},
    ]
    r = pac.compute_scope_creep(BASE, {"revised_completion_date": None}, history)
    assert r.status != "pass"
    assert "frequent" in r.evidence.lower()


def test_three_or_more_revisions_outweigh_two():
    two = pac.compute_scope_creep(BASE, {"revised_completion_date": None}, [
        {"reporting_date": date(2026, 3, 31), "revised_completion_date": date(2026, 7, 1)},
        {"reporting_date": date(2026, 6, 30), "revised_completion_date": date(2026, 7, 20)},
    ])
    three = pac.compute_scope_creep(BASE, {"revised_completion_date": None}, [
        {"reporting_date": date(2026, 3, 31), "revised_completion_date": date(2026, 7, 1)},
        {"reporting_date": date(2026, 6, 30), "revised_completion_date": date(2026, 7, 20)},
        {"reporting_date": date(2026, 9, 30), "revised_completion_date": date(2026, 8, 5)},
    ])
    assert three.severity > two.severity


def test_frequency_and_magnitude_compound():
    """A current-period wide slip AND a history of frequent revision both fire - the
    combined severity must be higher than either check alone, the same additive
    compounding LNC-19's shareholding check already uses."""
    history = [
        {"reporting_date": date(2026, 3, 31), "revised_completion_date": date(2026, 7, 10)},
        {"reporting_date": date(2026, 6, 30), "revised_completion_date": date(2026, 7, 25)},
    ]
    magnitude_only = pac.compute_scope_creep(
        BASE, {"revised_completion_date": date(2026, 11, 1)})
    both = pac.compute_scope_creep(
        BASE, {"revised_completion_date": date(2026, 11, 1)}, history)
    assert both.severity > magnitude_only.severity
    assert "slip" in both.evidence.lower()
    assert "frequent" in both.evidence.lower()


# ------------------------------------------------------------------ API + runner round-trip
def test_appraisal_and_progress_submission_is_readable_by_the_runners_own_lookup(
        lane_c_client, token_for, tid):
    r1 = lane_c_client.post(
        f"/lane-c/{tid}/borrowers/{ACC}/project-appraisal",
        headers=token_for("tenant_admin"),
        data={"sanctioned_cost": "1000000.00", "sanctioned_completion_date": "2026-06-30"})
    assert r1.status_code == 200, r1.text
    assert r1.json()["resubmission"] is False

    r2 = lane_c_client.post(
        f"/lane-c/{tid}/borrowers/{ACC}/project-progress",
        headers=token_for("tenant_admin"),
        data={"reporting_date": "2026-03-31", "actual_cost_incurred": "1600000.00",
             "revised_completion_date": "2026-11-01", "notes": "Delay in civil works."})
    assert r2.status_code == 200, r2.text

    db = SessionLocal()
    try:
        baseline = _project_baseline(db, tid, ACC)
        progress = _project_progress(db, tid, ACC, date(2026, 3, 31))
    finally:
        db.close()
    assert baseline == {"sanctioned_cost_paise": 100_000_000,
                        "sanctioned_completion_date": date(2026, 6, 30)}
    assert progress["actual_cost_incurred_paise"] == 160_000_000
    assert progress["revised_completion_date"] == date(2026, 11, 1)

    cost_signal = pac.compute_cost_variance(baseline, progress)
    scope_signal = pac.compute_scope_creep(baseline, progress)
    assert cost_signal.status == "critical"  # ratio 1.6x - above the 1.5x critical cutoff
    assert scope_signal.status != "pass"


def test_progress_history_reads_back_every_real_submission_in_order(
        lane_c_client, token_for, tid):
    lane_c_client.post(f"/lane-c/{tid}/borrowers/{ACC}/project-appraisal",
                       headers=token_for("tenant_admin"),
                       data={"sanctioned_cost": "1000000.00",
                            "sanctioned_completion_date": "2026-06-30"})
    periods = [("2026-03-31", "2026-07-10"), ("2026-06-30", "2026-07-25"),
              ("2026-09-30", "2026-08-05")]
    for reporting_date, revised in periods:
        r = lane_c_client.post(
            f"/lane-c/{tid}/borrowers/{ACC}/project-progress",
            headers=token_for("tenant_admin"),
            data={"reporting_date": reporting_date, "actual_cost_incurred": "500000.00",
                 "revised_completion_date": revised})
        assert r.status_code == 200, r.text

    db = SessionLocal()
    try:
        baseline = _project_baseline(db, tid, ACC)
        history = _project_progress_history(db, tid, ACC)
    finally:
        db.close()
    assert [h["reporting_date"].isoformat() for h in history] == [p[0] for p in periods]

    signal = pac.compute_scope_creep(baseline, {"revised_completion_date": None}, history)
    assert signal.status != "pass"
    assert "frequent" in signal.evidence.lower()


def test_resubmitting_the_appraisal_overwrites_not_duplicates(lane_c_client, token_for, tid):
    data = {"sanctioned_cost": "1000000.00", "sanctioned_completion_date": "2026-06-30"}
    lane_c_client.post(f"/lane-c/{tid}/borrowers/{ACC}/project-appraisal",
                       headers=token_for("tenant_admin"), data=data)
    r2 = lane_c_client.post(
        f"/lane-c/{tid}/borrowers/{ACC}/project-appraisal",
        headers=token_for("tenant_admin"),
        data={"sanctioned_cost": "1200000.00", "sanctioned_completion_date": "2026-09-30"})
    assert r2.json()["resubmission"] is True

    db = SessionLocal()
    try:
        count = db.execute(text(
            "SELECT count(*) FROM lane_c.project_appraisals WHERE tenant_id = :t AND account = :a"),
            {"t": tid, "a": ACC}).scalar()
        baseline = _project_baseline(db, tid, ACC)
    finally:
        db.close()
    assert count == 1
    assert baseline["sanctioned_cost_paise"] == 120_000_000
