"""Reconciliation invariants - including proof that they FAIL on corrupted data.

A check suite that only ever passes is indistinguishable from one that does nothing, so
these tests deliberately break the data and assert the right checks fire.
"""
import pytest


def _recon(client, tid, headers, **params):
    r = client.get(f"/analytics/{tid}/reconciliation", headers=headers, params=params)
    assert r.status_code == 200, r.text
    return r.json()


def test_clean_data_reconciles(analytics_client, token_for, tid):
    out = _recon(analytics_client, tid, token_for("rbi_inspector"))
    failed = [c["id"] for c in out["checks"] if not c["passed"]]
    assert out["status"] == "pass", f"unexpected failures: {failed}"
    assert out["summary"]["total"] >= 16


@pytest.mark.parametrize("params", [
    {"rails": "UPI"},
    {"rfa_only": "true"},
    {"fmr_status": "filed"},
    {"states": "fraud_declared"},
    {"severities": "high", "rails": "IMPS"},
], ids=lambda p: ",".join(p))
def test_reconciles_under_filters(analytics_client, token_for, tid, params):
    """Consistency checks compare a metric against an independent query; both sides must
    see the same slice, or filtering produces false criticals."""
    out = _recon(analytics_client, tid, token_for("rbi_inspector"), **params)
    failed = [c["id"] for c in out["checks"] if not c["passed"]]
    assert out["status"] == "pass", f"filters {params} broke: {failed}"


def test_money_is_integer_paise(analytics_client, token_for, tid):
    r = analytics_client.get(f"/analytics/{tid}/board", headers=token_for("board"))
    for name, m in r.json()["metrics"].items():
        if m["unit"] == "inr_paise":
            assert isinstance(m["value"], int), f"{name} is not an exact integer"


def test_no_regulatory_figure_is_volatile(analytics_client, token_for, tid):
    """A NOW()-relative metric cannot be reproduced as-of a date, so it must never back a
    filed number."""
    out = _recon(analytics_client, tid, token_for("rbi_inspector"))
    check = next(c for c in out["checks"] if c["id"] == "R-10b")
    assert check["passed"]


def test_breaks_are_detected(analytics_client, token_for, tid):
    """Corrupt three specific things; exactly the matching checks must fail."""
    from scripts.generate_synthetic_data import inject_breaks
    from cp_common import SessionLocal

    before = _recon(analytics_client, tid, token_for("rbi_inspector"))
    assert before["status"] == "pass"

    db = SessionLocal()
    try:
        inject_breaks(db, tid)
    finally:
        db.close()

    after = _recon(analytics_client, tid, token_for("rbi_inspector"))
    failed = {c["id"] for c in after["checks"] if not c["passed"]}
    assert after["status"] == "fail"
    assert {"R-01", "R-03", "R-09"} <= failed, f"missed a planted break; saw {failed}"
    assert after["summary"]["critical_failures"] >= 2
