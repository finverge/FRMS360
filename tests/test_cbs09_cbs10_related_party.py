"""CBS-09 (substantial related-party transactions, RBI #37) and CBS-10 (floating
front/associate companies via diverted funds, RBI #16) - both read the SAME verified
related_party_register reference feed, deliberately not group_register's loose,
unverified accounts list. CBS-09 is the broader exposure ratio; CBS-10 is the narrower
subset where diverted funds specifically landed on a control-grade related party.
"""
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text

from cp_common.db import SessionLocal
from services.analytics_service.app.detection import cbs_features as cf

ACC = "AC-CBS0910-BR214"
RELATED = "AC-RELATED-PARTY"


@pytest.fixture(autouse=True)
def _clean(tid):
    yield
    db = SessionLocal()
    try:
        db.execute(text("DELETE FROM ingestion.cbs_events WHERE tenant_id = :t"),
                   {"t": tid})
        db.execute(text("DELETE FROM analytics.reference_entries "
                        "WHERE tenant_id = :t AND kind = 'related_party_register'"),
                   {"t": tid})
        db.execute(text("DELETE FROM analytics.reference_lists "
                        "WHERE tenant_id = :t AND kind = 'related_party_register'"),
                   {"t": tid})
        db.commit()
    finally:
        db.close()


def _ev(kind, amount, n=1, **extra):
    now = datetime.now(timezone.utc)
    p = {"event_id": f"E-{kind}-{n}", "kind": kind,
         "ts": (now - timedelta(days=5)).isoformat(), "account": ACC, "amount": amount}
    p.update(extra)
    return p


def _post(client, token_for, tid, payloads, role="tenant_admin"):
    return client.post(f"/ingest/{tid}/cbs-events", headers=token_for(role),
                       json={"events": [{"payload": p} for p in payloads]})


def _load_related_party(client, token_for, tid, entries):
    return client.post(f"/analytics/{tid}/reference/related_party_register",
                       headers=token_for("risk_manager"),
                       json={"version": "v1", "source": "test", "activate": True,
                             "entries": entries})


# ------------------------------------------------------------------ related_party_accounts()
def test_related_party_accounts_is_empty_with_no_register_loaded(tid):
    db = SessionLocal()
    try:
        assert cf.related_party_accounts(db, tid) == {}
    finally:
        db.close()


def test_related_party_accounts_reads_relationship_type(
        analytics_client, token_for, tid):
    _load_related_party(analytics_client, token_for, tid,
                        [{"key": RELATED, "attributes": {"relationship_type":
                                                         "common_director"}}])
    db = SessionLocal()
    try:
        accounts = cf.related_party_accounts(db, tid)
    finally:
        db.close()
    assert accounts.get(cf.normalise(RELATED)) == "common_director"


# ------------------------------------------------------------------ CBS-09/CBS-10 gating
def test_cbs09_and_cbs10_are_omitted_when_no_register_is_loaded(
        ingestion_client, token_for, tid):
    """The same silent-false-clean trap group_register/CBS-03 already has - an empty
    related_party dict must leave these two OUT of observe_loan's result entirely, not
    report a false 0.0."""
    _post(ingestion_client, token_for, tid, [
        _ev("loan_disbursement", "100000.00", n=1),
        _ev("loan_utilisation", "50000.00", n=2, counterparty_account=RELATED),
    ])
    db = SessionLocal()
    try:
        ctx = cf.load_loan_context(db, tid, [ACC], datetime.now(timezone.utc))
    finally:
        db.close()
    obs = cf.observe_loan(ctx[ACC], related_party_loaded=False)
    assert "CBS-09" not in obs
    assert "CBS-10" not in obs


def test_cbs09_measures_exposure_to_a_related_party_account(
        ingestion_client, token_for, tid):
    _post(ingestion_client, token_for, tid, [
        _ev("loan_utilisation", "50000.00", n=1, counterparty_account=RELATED),
        _ev("loan_utilisation", "50000.00", n=2, counterparty_account="AC-UNRELATED"),
    ])
    db = SessionLocal()
    try:
        ctx = cf.load_loan_context(
            db, tid, [ACC], datetime.now(timezone.utc),
            related_party={cf.normalise(RELATED): "shareholding_overlap"})
    finally:
        db.close()
    obs = cf.observe_loan(ctx[ACC], related_party_loaded=True)
    assert obs["CBS-09"] == pytest.approx(0.5)


def test_cbs10_fires_only_for_diverted_funds_to_a_control_grade_party(
        ingestion_client, token_for, tid):
    """Related-party exposure alone (no diversion) must not fire CBS-10 - only
    diverted funds landing on a control-grade relationship do."""
    _post(ingestion_client, token_for, tid, [
        _ev("loan_disbursement", "100000.00", n=1),
        _ev("loan_utilisation", "40000.00", n=2, counterparty_account=RELATED,
           within_sanctioned_purpose="false"),
    ])
    db = SessionLocal()
    try:
        ctx = cf.load_loan_context(
            db, tid, [ACC], datetime.now(timezone.utc),
            related_party={cf.normalise(RELATED): "common_director"})
    finally:
        db.close()
    obs = cf.observe_loan(ctx[ACC], related_party_loaded=True)
    assert obs["CBS-10"] == pytest.approx(0.4)


def test_cbs10_does_not_fire_for_a_related_but_non_control_party(
        ingestion_client, token_for, tid):
    _post(ingestion_client, token_for, tid, [
        _ev("loan_disbursement", "100000.00", n=1),
        _ev("loan_utilisation", "40000.00", n=2, counterparty_account=RELATED,
           within_sanctioned_purpose="false"),
    ])
    db = SessionLocal()
    try:
        ctx = cf.load_loan_context(
            db, tid, [ACC], datetime.now(timezone.utc),
            related_party={cf.normalise(RELATED): "shareholding_overlap"})
    finally:
        db.close()
    obs = cf.observe_loan(ctx[ACC], related_party_loaded=True)
    assert obs["CBS-10"] == 0.0


def test_cbs10_does_not_fire_for_a_related_party_within_sanctioned_purpose(
        ingestion_client, token_for, tid):
    """Even a control-grade related party is not itself the finding - diversion is.
    Within-purpose utilisation to a related party only feeds CBS-09, not CBS-10."""
    _post(ingestion_client, token_for, tid, [
        _ev("loan_disbursement", "100000.00", n=1),
        _ev("loan_utilisation", "40000.00", n=2, counterparty_account=RELATED,
           within_sanctioned_purpose="true"),
    ])
    db = SessionLocal()
    try:
        ctx = cf.load_loan_context(
            db, tid, [ACC], datetime.now(timezone.utc),
            related_party={cf.normalise(RELATED): "common_director"})
    finally:
        db.close()
    obs = cf.observe_loan(ctx[ACC], related_party_loaded=True)
    assert obs["CBS-10"] == 0.0
    # All utilisation in this batch went to the related party - CBS-09's denominator
    # is utilised_paise, not disbursed_paise, so this is a real 1.0, not 0.4.
    assert obs["CBS-09"] == pytest.approx(1.0)


# ------------------------------------------------------------------ dormant register
def test_cbs09_and_cbs10_are_reported_dormant_when_the_register_is_missing():
    why = cf.unmeasurable({"loan_utilisation", "loan_disbursement"},
                          has_group_register=True, has_related_party_register=False)
    assert "CBS-09" in why and "related-party register" in why["CBS-09"]
    assert "CBS-10" in why and "related-party register" in why["CBS-10"]
    clean = cf.unmeasurable({"loan_utilisation", "loan_disbursement"},
                            has_group_register=True, has_related_party_register=True)
    assert "CBS-09" not in clean
    assert "CBS-10" not in clean
