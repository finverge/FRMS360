"""The detection runtime, end to end: ingest a typology, score it, check what fired.

The two properties worth protecting here are easy to lose and hard to notice:

* the tenant's *configured* thresholds decide what fires - otherwise the console is
  decoration;
* a badly-tuned threshold genuinely misses fraud - otherwise the demo lies.

Both are tested by changing a threshold and asserting the outcome changes with it.
"""
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import delete, text

from cp_common.db import SessionLocal
from services.analytics_service.app.detection import run_until_empty
from services.analytics_service.app.models import FactAlert, FactCase, FactTransaction

HUB = "AC-DET-HUB-1"


@pytest.fixture
def clean_slate(tid):
    """Detection writes real facts, so each test cleans up after itself and leaves the
    demo corpus untouched."""
    yield
    db = SessionLocal()
    try:
        db.execute(text(
            "DELETE FROM analytics.fact_alert WHERE tenant_id = :t AND txn_id LIKE 'DET-%'"),
            {"t": tid})
        db.execute(text(
            "DELETE FROM analytics.fact_transaction "
            "WHERE tenant_id = :t AND txn_id LIKE 'DET-%'"), {"t": tid})
        db.execute(text(
            "DELETE FROM analytics.fact_case WHERE tenant_id = :t AND case_id IN ("
            "  SELECT case_id FROM analytics.fact_case c WHERE c.tenant_id = :t"
            "    AND NOT EXISTS (SELECT 1 FROM analytics.fact_alert a "
            "                    WHERE a.case_id = c.case_id))"), {"t": tid})
        db.execute(text(
            "DELETE FROM ingestion.raw_transaction "
            "WHERE tenant_id = :t AND source_txn_id LIKE 'DET-%'"), {"t": tid})
        db.commit()
    finally:
        db.close()


def _fan_in(tid, ingestion_client, token_for, tag: str, payers: int = 46) -> int:
    """A mule collection account: many unrelated payers into one hub."""
    now = datetime.now(timezone.utc)
    txns = [{"rail": "UPI", "payload": {
        "upiTransactionId": f"DET-{tag}-{i:04d}",
        "txnTimestamp": (now - timedelta(hours=5, minutes=i)).isoformat(),
        "amountRupees": "24000.00",
        "payerAccount": f"AC-DET-P{i:03d}", "payeeAccount": HUB,
        "region": "west", "device_id": f"DEV-{i}"}} for i in range(payers)]
    r = ingestion_client.post(f"/ingest/{tid}/transactions",
                              headers=token_for("tenant_admin"),
                              json={"transactions": txns, "source": "test"})
    assert r.status_code == 202, r.text
    return r.json()["accepted"]


def _run(tid):
    db = SessionLocal()
    try:
        return run_until_empty(db, tid, batch=500)
    finally:
        db.close()


def _set_threshold(config_client, token_for, tid, rule: str, value: float, version: str):
    cfgs = config_client.get(f"/configs/{tid}",
                             headers=token_for("tenant_admin")).json()
    active = next(c for c in cfgs if c["name"] == rule and c["status"] == "active")
    body = active["body"]
    for band in body["config"]["bands"]:
        if band.get("operative"):
            band["lowerLimit"] = value
    made = config_client.post(f"/configs/{tid}", headers=token_for("tenant_admin"),
                              json={"kind": "rule", "name": rule, "version": version,
                                    "body": body})
    assert made.status_code == 201, made.text
    config_id = made.json()["id"]
    # BR-715: activation is maker-checker - propose, then a different eligible actor
    # confirms. Only the confirmation makes the new threshold live.
    proposed = config_client.post(f"/configs/{tid}/{config_id}/activate",
                                  headers=token_for("tenant_admin"))
    assert proposed.status_code == 200, proposed.text
    confirmed = config_client.post(f"/configs/{tid}/{config_id}/activate",
                                   headers=token_for("risk_manager"))
    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json()["status"] == "active"


# ------------------------------------------------------------------ the happy path
def test_a_mule_ring_is_detected_end_to_end(ingestion_client, config_client, token_for,
                                            tid, clean_slate):
    sent = _fan_in(tid, ingestion_client, token_for, "RING")
    rep = _run(tid)
    assert rep.projected == sent, f"{sent} sent, {rep.projected} projected"
    assert rep.alerts > 0, "a 46-payer fan-in produced no alerts at all"
    assert "LAY-02" in rep.fired_rules, \
        f"the fan-in hub rule did not fire; fired: {rep.fired_rules}"


def test_the_queue_is_drained_and_not_reprocessed(ingestion_client, token_for, tid,
                                                  clean_slate):
    _fan_in(tid, ingestion_client, token_for, "DRAIN", payers=6)
    first = _run(tid)
    second = _run(tid)
    assert first.projected == 6
    assert second.claimed == 0, "already-processed rows were handed out again"


def test_a_replayed_transaction_is_projected_once(ingestion_client, token_for, tid,
                                                  clean_slate):
    _fan_in(tid, ingestion_client, token_for, "REPLAY", payers=4)
    _run(tid)
    db = SessionLocal()
    try:
        n = db.execute(text(
            "SELECT COUNT(*) FROM analytics.fact_transaction "
            "WHERE tenant_id = :t AND txn_id LIKE 'DET-REPLAY-%'"), {"t": tid}).scalar()
    finally:
        db.close()
    assert n == 4, f"expected 4 projected transactions, found {n}"


# ------------------------------------------- the control plane actually controls it
def test_raising_a_threshold_stops_the_rule_firing(ingestion_client, config_client,
                                                   token_for, tid, clean_slate):
    """The claim the whole product rests on: retuning a band in the console changes
    detection, with no code change and no redeploy."""
    from services.analytics_service.app import rules as bridge

    _fan_in(tid, ingestion_client, token_for, "TUNE1")
    before = _run(tid)
    assert "LAY-02" in before.fired_rules, "baseline did not fire; test proves nothing"

    # 46 payers, so a threshold of 500 must silence it.
    _set_threshold(config_client, token_for, tid, "LAY-02", 500, "9.0.0")
    bridge.invalidate()

    _fan_in(tid, ingestion_client, token_for, "TUNE2")
    after = _run(tid)
    assert "LAY-02" not in after.fired_rules, \
        f"a threshold of 500 still fired on 46 counterparties: {after.fired_rules}"

    # Put it back, so a badly-tuned band is not left behind for the next test or demo.
    _set_threshold(config_client, token_for, tid, "LAY-02", 20, "9.0.1")
    bridge.invalidate()


def test_nothing_is_force_fired_when_the_catalogue_is_unreachable(tid, monkeypatch):
    """No catalogue must mean no scoring, not scoring against invented defaults.

    Falling back to built-in thresholds would produce alerts a bank never configured and
    cannot account for - worse than producing none.
    """
    from services.analytics_service.app import rules as bridge
    from services.analytics_service.app.detection import engine

    monkeypatch.setattr(engine, "active_rules", lambda *_a, **_k: {})
    db = SessionLocal()
    try:
        rep = engine.run_once(db, tid)
    finally:
        db.close()
    assert rep.claimed == 0 and rep.alerts == 0
    bridge.invalidate()


# ----------------------------------------------------------------------- honesty
def test_indicators_needing_external_data_are_reported_not_faked(ingestion_client,
                                                                 token_for, tid,
                                                                 clean_slate):
    """A sanctions rule with no sanctions list must stay silent and say why.

    Approximating it would put a number in front of an investigator that nothing
    supports, and would hide the gap from the coverage report an inspection reads.
    """
    from services.analytics_service.app.detection import features

    _fan_in(tid, ingestion_client, token_for, "HONEST", payers=3)
    rep = _run(tid)
    assert "CPT-02" in rep.unmeasurable, "the sanctions rule claims to be measurable"
    assert "sanctions" in rep.unmeasurable["CPT-02"].lower()
    for rule in rep.fired_rules:
        assert rule not in features.NEEDS_EXTERNAL_DATA, \
            f"{rule} fired despite needing data the platform does not have"


def test_computable_and_unmeasurable_rules_do_not_overlap():
    from services.analytics_service.app.detection import features
    overlap = set(features.COMPUTABLE) & set(features.NEEDS_EXTERNAL_DATA)
    assert not overlap, f"a rule is claimed both computable and not: {overlap}"


# -------------------------------------------------------------------- provenance
def test_live_traffic_is_distinguishable_from_the_demo_corpus(ingestion_client,
                                                              token_for, tid,
                                                              clean_slate):
    """The synthetic corpus must survive alongside real data and stay identifiable."""
    _fan_in(tid, ingestion_client, token_for, "PROV", payers=3)
    _run(tid)
    db = SessionLocal()
    try:
        sources = dict(db.execute(text(
            "SELECT source, COUNT(*) FROM analytics.fact_transaction "
            "WHERE tenant_id = :t GROUP BY source"), {"t": tid}).all())
    finally:
        db.close()
    assert sources.get("synthetic", 0) > 0, "the demo corpus lost its label"
    assert sources.get("test", 0) == 3, f"ingested rows not labelled: {sources}"


# ------------------------------------------------------------- the R-03 invariant
def test_a_case_value_ties_to_its_distinct_transactions(ingestion_client, token_for,
                                                        tid, clean_slate):
    """One transaction firing several rules gets several alerts. Its value must still
    be counted once - the flaw that made R-03 fail the first time live data ran."""
    _fan_in(tid, ingestion_client, token_for, "R03")
    _run(tid)
    db = SessionLocal()
    try:
        bad = db.execute(text(
            "SELECT COUNT(*) FROM ("
            "  SELECT c.case_id, c.amount_paise AS stated, COALESCE(("
            "    SELECT SUM(t.amount_paise) FROM ("
            "      SELECT DISTINCT a.txn_id FROM analytics.fact_alert a"
            "      WHERE a.case_id = c.case_id AND a.tenant_id = c.tenant_id) d"
            "    JOIN analytics.fact_transaction t"
            "      ON t.txn_id = d.txn_id AND t.tenant_id = c.tenant_id), 0) AS derived"
            "  FROM analytics.fact_case c WHERE c.tenant_id = :t"
            ") s WHERE s.stated <> s.derived"), {"t": tid}).scalar()
    finally:
        db.close()
    assert bad == 0, f"{bad} case(s) do not tie to their evidence"


def test_creditor_side_rules_are_all_greater_than_rules():
    """The two perspectives are merged with max(), which assumes higher = riskier.

    An "lte" rule such as beneficiary age would be silently inverted by that merge - the
    safer of the two readings would win and the rule would quietly stop firing. Nothing
    in the merge can detect this, so the constraint is asserted here instead.
    """
    from services.config_service.app.ews_catalogue import comparator_for
    from services.analytics_service.app.detection.features import CREDITOR_SIDE

    wrong = [r for r in CREDITOR_SIDE if comparator_for(r) != "gte"]
    assert not wrong, (
        f"{wrong} fire below their threshold, so merging both sides with max() would "
        "make them harder to trip, not easier. Merge those with min().")
