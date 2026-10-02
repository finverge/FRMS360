"""CPT-04 (dispute on title of collateral securities, RBI 2016 illustrative signal #9) -
reuses CPT-03's exact collateral_ref plumbing but reads a different reference feed
(title_disputes) with a different fact shape: presence of a matching entry IS the
finding (a flag), not a count the way CPT-03's lender_count is.
"""
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text

from cp_common.db import SessionLocal
from services.analytics_service.app.detection import cbs_features as cf
from services.analytics_service.app.detection import reference as ref
from services.analytics_service.app.detection.features import AccountContext, observe

ACC = "AC-CPT04-BR214"


@pytest.fixture(autouse=True)
def _clean(tid):
    yield
    db = SessionLocal()
    try:
        db.execute(text("DELETE FROM analytics.reference_entries "
                        "WHERE tenant_id = :t AND kind = 'title_disputes'"), {"t": tid})
        db.execute(text("DELETE FROM analytics.reference_lists "
                        "WHERE tenant_id = :t AND kind = 'title_disputes'"), {"t": tid})
        db.commit()
    finally:
        db.close()


def _txn(collateral_ref=None):
    now = datetime.now(timezone.utc)
    t = {"ts": now, "amount_paise": 1000, "rail": "UPI",
        "debtor_account": ACC, "creditor_account": "AC-OTHER", "device_id": ""}
    if collateral_ref is not None:
        t["collateral_ref"] = collateral_ref
    return t


def _load_disputes(client, token_for, tid, entries):
    return client.post(f"/analytics/{tid}/reference/title_disputes",
                       headers=token_for("risk_manager"),
                       json={"version": "v1", "source": "test", "activate": True,
                             "entries": entries})


def test_no_collateral_ref_in_the_batch_is_unmeasured(analytics_client, token_for, tid):
    _load_disputes(analytics_client, token_for, tid,
                   [{"key": "PROP-REG-00123", "attributes": {"detail": "Civil suit pending"}}])
    db = SessionLocal()
    try:
        screening = ref.load_screening_set(db, tid)
    finally:
        db.close()
    out = observe(_txn(collateral_ref=None), AccountContext(), {}, datetime.now(timezone.utc),
                 cycles={}, screening=screening)
    assert "CPT-04" not in out


def test_collateral_ref_present_but_no_title_dispute_list_is_unmeasured(
        analytics_client, token_for, tid):
    db = SessionLocal()
    try:
        screening = ref.load_screening_set(db, tid)
    finally:
        db.close()
    out = observe(_txn(collateral_ref="PROP-REG-00123"), AccountContext(), {},
                 datetime.now(timezone.utc), cycles={}, screening=screening)
    assert "CPT-04" not in out


def test_both_present_with_a_matching_key_fires(analytics_client, token_for, tid):
    _load_disputes(analytics_client, token_for, tid,
                   [{"key": "PROP-REG-00123", "attributes": {"detail": "Civil suit pending"}}])
    db = SessionLocal()
    try:
        screening = ref.load_screening_set(db, tid)
    finally:
        db.close()
    out = observe(_txn(collateral_ref="PROP-REG-00123"), AccountContext(), {},
                 datetime.now(timezone.utc), cycles={}, screening=screening)
    assert out["CPT-04"] == 1.0


def test_both_present_with_a_non_matching_key_is_a_real_pass_not_unmeasured(
        analytics_client, token_for, tid):
    """A loaded register that simply doesn't cover this asset is a real, measured zero -
    not the same as no register existing at all."""
    _load_disputes(analytics_client, token_for, tid,
                   [{"key": "PROP-REG-00123", "attributes": {"detail": "Civil suit pending"}}])
    db = SessionLocal()
    try:
        screening = ref.load_screening_set(db, tid)
    finally:
        db.close()
    out = observe(_txn(collateral_ref="SOME-OTHER-ASSET"), AccountContext(), {},
                 datetime.now(timezone.utc), cycles={}, screening=screening)
    assert out["CPT-04"] == 0.0


def test_cpt04_is_reported_dormant_for_the_cbs_feed_reason_when_that_is_the_gap(tid):
    seen_kinds = set()
    reasons = cf.unmeasurable(seen_kinds, has_group_register=True)
    assert "CPT-04" in reasons
    assert "collateral_valuation" in reasons["CPT-04"]
