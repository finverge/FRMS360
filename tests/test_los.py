"""BR-214: loan-origination fraud - falsified applications, straw borrowers, collusive
valuations. Verified against RBI's own 45-item illustrative EWS list (mostly post-sanction
monitoring, already covered by the existing catalogue) plus documented straw-borrower and
appraisal-collusion typologies; the genuine gap was pre-disbursement, which is what this
covers.

The property that matters most here, and the one the architecture note in cbs_models.py
and engine.py exists to protect: **a finding must not require a payment transaction to
exist.** BEH-02/03 and CBS-01/02/03 piggyback on one; a loan application usually has none
yet, and may never have one if the application is refused. Several tests below construct
exactly that case - zero fact_transaction rows - and check detection still works.
"""
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text

from cp_common.db import SessionLocal
from services.analytics_service.app.detection import engine as det_engine
from services.analytics_service.app.detection import los_features
from services.analytics_service.app.models import FactAlert, FactCase, FactTransaction
from services.ingestion_service.app.adapters import AdapterError
from services.ingestion_service.app.cbs_adapter import adapt_cbs

APP_ACCOUNT = "AC-LOS-APP-001"


@pytest.fixture(autouse=True)
def _clean(tid):
    yield
    db = SessionLocal()
    try:
        db.execute(text("DELETE FROM analytics.fact_alert WHERE tenant_id = :t "
                        "AND txn_id LIKE 'APP-%'"), {"t": tid})
        db.execute(text("DELETE FROM analytics.fact_case WHERE tenant_id = :t "
                        "AND rail = 'LOS'"), {"t": tid})
        db.execute(text("DELETE FROM ingestion.cbs_events WHERE tenant_id = :t "
                        "AND kind IN ('loan_application', 'collateral_valuation')"),
                   {"t": tid})
        db.commit()
    finally:
        db.close()


def _app(account, *, n, declared_income=None, applicant_id=None, amount="500000",
        source="live", ts=None):
    now = datetime.now(timezone.utc)
    p = {"event_id": f"E-LOS-APP-{account}-{n}", "kind": "loan_application",
         "ts": (ts or (now - timedelta(hours=1))).isoformat(),
         "account": account, "amount": amount}
    if declared_income is not None:
        p["declared_income_paise"] = declared_income
    if applicant_id is not None:
        p["applicant_id"] = applicant_id
    return p


def _valuation(account, *, n, valuer_id, asset_type="residential_flat", amount="1000000",
              ts=None):
    now = datetime.now(timezone.utc)
    return {"event_id": f"E-LOS-VAL-{account}-{n}", "kind": "collateral_valuation",
            "ts": (ts or (now - timedelta(hours=1))).isoformat(),
            "account": account, "amount": amount, "asset_type": asset_type,
            "valuer_id": valuer_id}


def _post(client, token_for, tid, payloads, role="tenant_admin", source="live"):
    return client.post(f"/ingest/{tid}/cbs-events", headers=token_for(role),
                       json={"source": source,
                             "events": [{"payload": p} for p in payloads]})


def _credit(tid, account, amount_paise, *, days_ago=30):
    """A real credit on the applicant's own account, so LOS-01 has a denominator."""
    db = SessionLocal()
    try:
        db.add(FactTransaction(
            txn_id=f"LOS-CREDIT-{account}-{days_ago}", tenant_id=tid,
            ts=datetime.now(timezone.utc) - timedelta(days=days_ago), rail="IMPS",
            amount_paise=amount_paise, debtor_account="AC-LOS-PAYER",
            creditor_account=account, branch="", region="", product="savings",
            customer_segment="retail", channel="", device_id="", ip_addr="",
            status="settled", source="live"))
        db.commit()
    finally:
        db.close()


@pytest.fixture(autouse=True)
def _clean_credits(tid):
    yield
    db = SessionLocal()
    try:
        db.execute(text("DELETE FROM analytics.fact_transaction WHERE tenant_id = :t "
                        "AND txn_id LIKE 'LOS-CREDIT-%'"), {"t": tid})
        db.commit()
    finally:
        db.close()


# ------------------------------------------------------------------ the adapter
def test_loan_application_does_not_require_declared_income_or_identity():
    """Their absence means those two specific measurements are unavailable for this
    application - not that the application itself is unusable."""
    out = adapt_cbs({"event_id": "1", "kind": "loan_application",
                     "ts": "2026-08-01T00:00:00Z", "account": APP_ACCOUNT,
                     "amount": "500000"})
    assert out["kind"] == "loan_application"
    assert "declared_income_paise" not in out["attributes"]


def test_collateral_valuation_requires_asset_type():
    with pytest.raises(AdapterError) as exc:
        adapt_cbs({"event_id": "1", "kind": "collateral_valuation",
                  "ts": "2026-08-01T00:00:00Z", "account": APP_ACCOUNT,
                  "amount": "1000000", "valuer_id": "V-1"})
    assert "asset_type" in str(exc.value)


def test_collateral_valuation_requires_valuer_id():
    with pytest.raises(AdapterError) as exc:
        adapt_cbs({"event_id": "1", "kind": "collateral_valuation",
                  "ts": "2026-08-01T00:00:00Z", "account": APP_ACCOUNT,
                  "amount": "1000000", "asset_type": "residential_flat"})
    assert "valuer_id" in str(exc.value)


def test_collateral_valuation_is_accepted_with_both():
    out = adapt_cbs({"event_id": "1", "kind": "collateral_valuation",
                     "ts": "2026-08-01T00:00:00Z", "account": APP_ACCOUNT,
                     "amount": "1000000", "asset_type": "Residential Flat",
                     "valuer_id": "V-1"})
    assert out["attributes"]["asset_type"] == "residential flat"  # normalised
    assert out["attributes"]["valuer_id"] == "V-1"


# ------------------------------------------------------- LOS-01: falsified application
def test_los01_fires_when_declared_income_far_exceeds_observed_activity(
        ingestion_client, token_for, tid):
    _credit(tid, APP_ACCOUNT, 20_000_00)  # Rs 20,000 total credited over 30 days
    r = _post(ingestion_client, token_for, tid,
             [_app(APP_ACCOUNT, n=1, declared_income=500_000_00)])  # Rs 5,00,000 declared
    assert r.status_code == 202, r.text

    db = SessionLocal()
    try:
        event = db.execute(text(
            "SELECT id, account, amount_paise, attributes, source FROM ingestion.cbs_events "
            "WHERE tenant_id = :t AND kind = 'loan_application' AND account = :a"),
            {"t": tid, "a": APP_ACCOUNT}).mappings().first()
        obs = los_features.observe_application(db, tid, dict(event),
                                                now=datetime.now(timezone.utc))
    finally:
        db.close()
    assert obs["LOS-01"] > 3.0, "declared income 25x observed activity should score high"


def test_los01_is_unmeasurable_with_no_account_history(ingestion_client, token_for, tid):
    """A brand-new-to-bank applicant has no denominator - unmeasurable, not clean."""
    r = _post(ingestion_client, token_for, tid,
             [_app(APP_ACCOUNT, n=1, declared_income=500_000_00)])
    assert r.status_code == 202, r.text

    db = SessionLocal()
    try:
        event = db.execute(text(
            "SELECT id, account, amount_paise, attributes, source FROM ingestion.cbs_events "
            "WHERE tenant_id = :t AND kind = 'loan_application' AND account = :a"),
            {"t": tid, "a": APP_ACCOUNT}).mappings().first()
        obs = los_features.observe_application(db, tid, dict(event),
                                                now=datetime.now(timezone.utc))
    finally:
        db.close()
    assert "LOS-01" not in obs


# ----------------------------------------------------------- LOS-03: straw borrower
def test_los03_counts_other_applications_from_the_same_identity(
        ingestion_client, token_for, tid):
    r = _post(ingestion_client, token_for, tid, [
        _app(f"{APP_ACCOUNT}-A", n=1, applicant_id="PAN-XYZ123"),
        _app(f"{APP_ACCOUNT}-B", n=2, applicant_id="PAN-XYZ123"),
        _app(f"{APP_ACCOUNT}-C", n=3, applicant_id="PAN-XYZ123"),
    ])
    assert r.status_code == 202, r.text

    db = SessionLocal()
    try:
        event = db.execute(text(
            "SELECT id, account, amount_paise, attributes, source FROM ingestion.cbs_events "
            "WHERE tenant_id = :t AND kind = 'loan_application' AND account = :a"),
            {"t": tid, "a": f"{APP_ACCOUNT}-A"}).mappings().first()
        obs = los_features.observe_application(db, tid, dict(event),
                                                now=datetime.now(timezone.utc))
    finally:
        db.close()
    assert obs["LOS-03"] == 2.0, "2 other applications from the same identity"


def test_los03_is_unmeasurable_with_no_applicant_identity(ingestion_client, token_for, tid):
    r = _post(ingestion_client, token_for, tid, [_app(APP_ACCOUNT, n=1)])
    assert r.status_code == 202, r.text
    db = SessionLocal()
    try:
        event = db.execute(text(
            "SELECT id, account, amount_paise, attributes, source FROM ingestion.cbs_events "
            "WHERE tenant_id = :t AND kind = 'loan_application' AND account = :a"),
            {"t": tid, "a": APP_ACCOUNT}).mappings().first()
        obs = los_features.observe_application(db, tid, dict(event),
                                                now=datetime.now(timezone.utc))
    finally:
        db.close()
    assert "LOS-03" not in obs


# --------------------------------------------------------- LOS-02: collusive valuation
def test_los02_is_unmeasurable_below_the_minimum_peer_sample(
        ingestion_client, token_for, tid):
    r = _post(ingestion_client, token_for, tid, [
        _valuation(f"{APP_ACCOUNT}-V1", n=1, valuer_id="PEER-1", amount="1000000"),
    ])
    assert r.status_code == 202, r.text
    db = SessionLocal()
    try:
        event = db.execute(text(
            "SELECT id, account, amount_paise, attributes, source FROM ingestion.cbs_events "
            "WHERE tenant_id = :t AND kind = 'collateral_valuation' AND account = :a"),
            {"t": tid, "a": f"{APP_ACCOUNT}-V1"}).mappings().first()
        obs = los_features.observe_valuation(db, tid, dict(event),
                                             now=datetime.now(timezone.utc))
    finally:
        db.close()
    assert obs == {}


def test_los02_fires_when_a_valuer_runs_high_against_peers(ingestion_client, token_for, tid):
    peers = [_valuation(f"{APP_ACCOUNT}-PEER{i}", n=i, valuer_id=f"PEER-{i}",
                        amount="1000000") for i in range(1, 6)]
    r = _post(ingestion_client, token_for, tid, peers)
    assert r.status_code == 202, r.text

    r2 = _post(ingestion_client, token_for, tid, [
        _valuation(f"{APP_ACCOUNT}-SUBJECT", n=99, valuer_id="V-SUSPECT",
                  amount="1800000"),  # 80% above the Rs 10,00,000 peer average
    ])
    assert r2.status_code == 202, r2.text

    db = SessionLocal()
    try:
        event = db.execute(text(
            "SELECT id, account, amount_paise, attributes, source FROM ingestion.cbs_events "
            "WHERE tenant_id = :t AND kind = 'collateral_valuation' AND account = :a"),
            {"t": tid, "a": f"{APP_ACCOUNT}-SUBJECT"}).mappings().first()
        obs = los_features.observe_valuation(db, tid, dict(event),
                                             now=datetime.now(timezone.utc))
    finally:
        db.close()
    assert obs["LOS-02"] == pytest.approx(1.8, rel=0.01)


# --------------------------------------------------------------- evaluate_los(): end to end
def test_evaluate_los_creates_an_alert_with_no_payment_transaction_at_all(
        ingestion_client, token_for, tid):
    """The whole architectural point of BR-214: a straw-borrower finding at application
    time, before a rupee has moved and possibly before it ever does."""
    r = _post(ingestion_client, token_for, tid, [
        _app(f"{APP_ACCOUNT}-X", n=1, applicant_id="PAN-STRAW-1"),
        _app(f"{APP_ACCOUNT}-Y", n=2, applicant_id="PAN-STRAW-1"),
        _app(f"{APP_ACCOUNT}-Z", n=3, applicant_id="PAN-STRAW-1"),
    ])
    assert r.status_code == 202, r.text

    db = SessionLocal()
    try:
        txns_before = db.scalar(text(
            "SELECT COUNT(*) FROM analytics.fact_transaction WHERE tenant_id = :t "
            "AND (debtor_account = :a OR creditor_account = :a)"),
            {"t": tid, "a": f"{APP_ACCOUNT}-X"})
        assert txns_before == 0, "test setup should have zero payment transactions"

        rep = det_engine.evaluate_los(db, tid)
        assert rep.evaluated == 3
        assert rep.alerts >= 1
        assert "LOS-03" in rep.fired_rules

        alert = db.execute(text(
            "SELECT alert_id, txn_id, rule_id FROM analytics.fact_alert "
            "WHERE tenant_id = :t AND rule_id = 'LOS-03' AND txn_id LIKE 'APP-%'"),
            {"t": tid}).mappings().first()
        assert alert is not None, "no LOS-03 alert was recorded at all"
        # The alert is addressed to a synthetic application reference, not a real
        # transaction - and detection did not need one to fire.
        ft = db.get(FactTransaction, alert["txn_id"])
    finally:
        db.close()
    assert ft is None, "a LOS alert must not require a fabricated transaction row"


def test_evaluate_los_is_idempotent_across_repeated_calls(ingestion_client, token_for, tid):
    """An application is evaluated once. A second run must not re-alert on it."""
    r = _post(ingestion_client, token_for, tid, [
        _app(f"{APP_ACCOUNT}-I1", n=1, applicant_id="PAN-IDEMPOTENT"),
        _app(f"{APP_ACCOUNT}-I2", n=2, applicant_id="PAN-IDEMPOTENT"),
    ])
    assert r.status_code == 202, r.text

    db = SessionLocal()
    try:
        first = det_engine.evaluate_los(db, tid)
        second = det_engine.evaluate_los(db, tid)
    finally:
        db.close()
    assert first.evaluated == 2
    assert second.evaluated == 0, "already-processed applications must not be reclaimed"


def test_evaluate_los_returns_early_with_no_catalogue(monkeypatch, tid):
    monkeypatch.setattr(det_engine, "active_rules", lambda t: {})
    db = SessionLocal()
    try:
        rep = det_engine.evaluate_los(db, tid)
    finally:
        db.close()
    assert rep.evaluated == 0


def test_run_once_evaluates_los_even_with_no_pending_payment_transactions(
        ingestion_client, token_for, tid, monkeypatch):
    """Regression guard for the bug this file's own docstring warns about: evaluate_los
    must not be skipped just because the payment queue is empty."""
    from services.analytics_service.app.detection import engine as eng

    r = _post(ingestion_client, token_for, tid, [
        _app(f"{APP_ACCOUNT}-R1", n=1, applicant_id="PAN-RUNONCE"),
        _app(f"{APP_ACCOUNT}-R2", n=2, applicant_id="PAN-RUNONCE"),
        _app(f"{APP_ACCOUNT}-R3", n=3, applicant_id="PAN-RUNONCE"),
    ])
    assert r.status_code == 202, r.text

    # Force the payment claim to look empty, isolating this test from whatever is (or
    # is not) queued on the payment side.
    monkeypatch.setattr(eng, "_internal",
                        lambda path, body: {"claim_token": "t", "rows": []})

    db = SessionLocal()
    try:
        rep = eng.run_once(db, tid)
    finally:
        db.close()
    assert rep.claimed == 0
    assert "LOS-03" in rep.fired_rules, \
        "evaluate_los did not run when the payment queue was empty"
