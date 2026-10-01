"""CPT-03 (collateral charged to multiple lenders) needs two independent things before
it can ever fire: a loaded CERSAI reference list, AND a transaction whose batch carries a
matchable collateral_ref at all. Before this file, no CBS event kind carried the second
one - collateral_valuation only ever carried asset_type (a category for LOS-02's peer
average, not a specific asset key) and valuer_id. These tests hold both halves of that
gap, and the four real states a batch can be in.
"""
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text

from cp_common.db import SessionLocal
from services.analytics_service.app.detection import cbs_features as cf
from services.analytics_service.app.detection import reference as ref
from services.analytics_service.app.detection.features import AccountContext, observe
from services.ingestion_service.app.adapters import AdapterError
from services.ingestion_service.app.cbs_adapter import adapt_cbs

ACC = "AC-CPT03-BR214"


@pytest.fixture(autouse=True)
def _clean(tid):
    yield
    db = SessionLocal()
    try:
        db.execute(text("DELETE FROM ingestion.cbs_events WHERE tenant_id = :t"),
                   {"t": tid})
        db.execute(text("DELETE FROM analytics.reference_entries "
                        "WHERE tenant_id = :t AND kind = 'cersai_charges'"), {"t": tid})
        db.execute(text("DELETE FROM analytics.reference_lists "
                        "WHERE tenant_id = :t AND kind = 'cersai_charges'"), {"t": tid})
        db.commit()
    finally:
        db.close()


def _post(client, token_for, tid, payloads, role="tenant_admin"):
    return client.post(f"/ingest/{tid}/cbs-events", headers=token_for(role),
                       json={"events": [{"payload": p} for p in payloads]})


def _valuation_event(n=1, collateral_id=None):
    now = datetime.now(timezone.utc)
    p = {"event_id": f"E-cv-{n}", "kind": "collateral_valuation",
         "ts": (now - timedelta(days=5)).isoformat(), "account": ACC,
         "amount": "1000000", "asset_type": "residential_property", "valuer_id": "VAL-01"}
    if collateral_id is not None:
        p["collateral_id"] = collateral_id
    return p


def _txn(collateral_ref=None):
    now = datetime.now(timezone.utc)
    t = {"ts": now, "amount_paise": 1000, "rail": "UPI",
        "debtor_account": ACC, "creditor_account": "AC-OTHER", "device_id": ""}
    if collateral_ref is not None:
        t["collateral_ref"] = collateral_ref
    return t


def _load_cersai(client, token_for, tid, entries):
    return client.post(f"/analytics/{tid}/reference/cersai_charges",
                       headers=token_for("risk_manager"),
                       json={"version": "v1", "source": "test", "activate": True,
                             "entries": entries})


# ------------------------------------------------------------------ the adapter
def test_collateral_id_is_optional_on_a_valuation_event(tid):
    """Most CBS extracts don't send this yet - absent must not be refused."""
    out = adapt_cbs(_valuation_event(collateral_id=None))
    assert "collateral_id" not in out["attributes"]


def test_a_given_collateral_id_is_carried_through(tid):
    out = adapt_cbs(_valuation_event(collateral_id="PROP-REG-00123"))
    assert out["attributes"]["collateral_id"] == "PROP-REG-00123"


# ------------------------------------------------------------------ LoanContext
def test_load_loan_context_captures_the_collateral_id(ingestion_client, token_for, tid):
    _post(ingestion_client, token_for, tid, [_valuation_event(collateral_id="PROP-REG-00123")])
    db = SessionLocal()
    try:
        ctx = cf.load_loan_context(db, tid, [ACC], datetime.now(timezone.utc))
    finally:
        db.close()
    assert ctx[ACC].collateral_id == "PROP-REG-00123"


def test_no_valuation_event_leaves_collateral_id_absent(tid):
    db = SessionLocal()
    try:
        ctx = cf.load_loan_context(db, tid, [ACC], datetime.now(timezone.utc))
    finally:
        db.close()
    assert ctx[ACC].collateral_id is None


# ------------------------------------------------------------------ the four real states
def test_no_collateral_ref_in_the_batch_is_unmeasured(analytics_client, token_for, tid):
    _load_cersai(analytics_client, token_for, tid,
                [{"key": "PROP-REG-00123", "attributes": {"lenders": ["Bank A", "Bank B"]}}])
    db = SessionLocal()
    try:
        screening = ref.load_screening_set(db, tid)
    finally:
        db.close()
    out = observe(_txn(collateral_ref=None), AccountContext(), {}, datetime.now(timezone.utc),
                 cycles={}, screening=screening)
    assert "CPT-03" not in out


def test_collateral_ref_present_but_no_cersai_list_is_unmeasured(analytics_client, token_for, tid):
    db = SessionLocal()
    try:
        screening = ref.load_screening_set(db, tid)
    finally:
        db.close()
    out = observe(_txn(collateral_ref="PROP-REG-00123"), AccountContext(), {},
                 datetime.now(timezone.utc), cycles={}, screening=screening)
    assert "CPT-03" not in out


def test_both_present_with_a_matching_key_fires(analytics_client, token_for, tid):
    _load_cersai(analytics_client, token_for, tid,
                [{"key": "PROP-REG-00123", "attributes": {"lenders": ["Bank A", "Bank B"]}}])
    db = SessionLocal()
    try:
        screening = ref.load_screening_set(db, tid)
    finally:
        db.close()
    out = observe(_txn(collateral_ref="PROP-REG-00123"), AccountContext(), {},
                 datetime.now(timezone.utc), cycles={}, screening=screening)
    assert out["CPT-03"] == 2.0


def test_both_present_with_a_non_matching_key_is_a_real_pass_not_unmeasured(
        analytics_client, token_for, tid):
    """A loaded list that simply doesn't cover this asset is a real, measured zero - not
    the same as no list existing at all."""
    _load_cersai(analytics_client, token_for, tid,
                [{"key": "PROP-REG-00123", "attributes": {"lenders": ["Bank A", "Bank B"]}}])
    db = SessionLocal()
    try:
        screening = ref.load_screening_set(db, tid)
    finally:
        db.close()
    out = observe(_txn(collateral_ref="SOME-OTHER-ASSET"), AccountContext(), {},
                 datetime.now(timezone.utc), cycles={}, screening=screening)
    assert out["CPT-03"] == 0.0


# ------------------------------------------------------------------ dormant register
def test_cpt03_is_reported_dormant_for_the_cbs_feed_reason_when_that_is_the_gap(tid):
    """No collateral_valuation event at all, no CERSAI list either - the CBS-feed reason
    is surfaced (checked first), not silently dropped in favour of the reference-data
    reason, and not overwritten by it either."""
    seen_kinds = set()
    reasons = cf.unmeasurable(seen_kinds, has_group_register=True)
    assert "CPT-03" in reasons
    assert "collateral_valuation" in reasons["CPT-03"]
