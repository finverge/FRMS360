"""BR-611: live traffic and demonstration data must be separable, and returns must be live.

Two obligations are tested here, and they are deliberately different in strength.

*Analytical* views may include demonstration data — a pilot and a demo routinely share an
instance — but they must be able to exclude it and must disclose the mix. That is a
filter, and filters are tested by arithmetic: the parts sum to the whole.

A *regulatory return* has no such latitude. Filing the demonstration corpus with the
Reserve Bank is a false submission, so this is a refusal rather than a default, and the
test that matters is the one proving the refusal actually fires — a guard nobody has seen
say no is an assumption.
"""
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text

from cp_common.db import SessionLocal
from services.analytics_service.app import data_source
from services.analytics_service.app.engine.base import Filters
from services.analytics_service.app.engine.postgres import PostgresEngine


# --------------------------------------------------------------- the vocabulary
def test_only_live_counts_as_live():
    assert data_source.is_live("live")
    assert data_source.is_live("  LIVE  ")
    for other in ("synthetic", "replay", "test", "", None, "LIVEISH"):
        assert not data_source.is_live(other), f"{other!r} must not read as live"


def test_unknown_provenance_is_treated_as_not_live():
    """A row we cannot vouch for is exactly the row that must not reach a return."""
    assert data_source.non_live(["live", "wat"]) == ["wat"]
    assert data_source.non_live(["live"]) == []
    assert data_source.non_live(["synthetic", "replay", "live"]) == ["synthetic", "replay"]


# ------------------------------------------------------------------- the filter
@pytest.fixture
def provenance_rows(tid):
    """Two live rows and three synthetic ones, in one tenant."""
    db = SessionLocal()
    made = []
    try:
        now = datetime.now(timezone.utc)
        for i, src in enumerate(["live", "live", "synthetic", "synthetic", "synthetic"]):
            txn = f"TXN-PROV-{i}"
            made.append(txn)
            db.execute(text(
                "INSERT INTO analytics.fact_transaction (txn_id, tenant_id, ts, rail, "
                "amount_paise, debtor_account, creditor_account, branch, region, product, "
                "customer_segment, channel, device_id, ip_addr, status, source) VALUES "
                "(:x, :t, :ts, 'UPI', 100000, 'AC-P-DR', 'AC-P-CR', 'BR1', 'south', "
                "'savings', 'retail', 'mobile', 'D1', '10.0.0.1', 'settled', :s)"),
                {"x": txn, "t": tid, "ts": now - timedelta(hours=i + 1), "s": src})
        db.commit()
        yield tid
    finally:
        for txn in made:
            db.execute(text("DELETE FROM analytics.fact_transaction WHERE txn_id = :x"),
                       {"x": txn})
        db.commit()
        db.close()


def _count(tid, sources):
    db = SessionLocal()
    try:
        return PostgresEngine(db).metric(
            "transaction_count", Filters(tenant_id=tid, sources=sources)).value
    finally:
        db.close()


def test_the_filter_actually_partitions(provenance_rows):
    """Live + synthetic must equal unfiltered. A filter that drops rows is worse than none."""
    everything = _count(provenance_rows, [])
    live = _count(provenance_rows, ["live"])
    synthetic = _count(provenance_rows, ["synthetic"])

    assert live >= 2 and synthetic >= 3, "fixture rows are missing"
    assert live + synthetic == everything, (
        f"live ({live}) + synthetic ({synthetic}) != all ({everything}); "
        "the provenance filter is not a partition")


def test_filtering_to_live_excludes_the_demo_corpus(provenance_rows):
    assert _count(provenance_rows, ["live"]) < _count(provenance_rows, []), (
        "filtering to live changed nothing — either the filter is not applied or this "
        "tenant has no demonstration data, and both make the test meaningless")


def test_the_breakdown_sums_to_the_total(provenance_rows):
    """R-06 for the new dimension: a breakdown that does not sum hides a bucket."""
    db = SessionLocal()
    try:
        eng = PostgresEngine(db)
        f = Filters(tenant_id=provenance_rows)
        rows = eng.breakdown("transaction_count", "source", f)
        assert sum(r["value"] for r in rows) == eng.metric("transaction_count", f).value
    finally:
        db.close()


# ------------------------------------------------------- the refusal that matters
def _seed_case(tid, source, evidence_source):
    """One declared, fully evidenced case whose provenance we control."""
    db = SessionLocal()
    cid = f"C-PROV-{source}-{evidence_source}"
    try:
        now = datetime.now(timezone.utc)
        db.execute(text(
            "INSERT INTO analytics.fact_transaction (txn_id, tenant_id, ts, rail, "
            "amount_paise, debtor_account, creditor_account, branch, region, product, "
            "customer_segment, channel, device_id, ip_addr, status, source) VALUES "
            "(:x, :t, :ts, 'UPI', 987127, 'AC-PV-DR', 'AC-PV-CR', 'BR1', 'south', "
            "'savings', 'retail', 'mobile', 'D1', '10.0.0.1', 'settled', :s)"),
            {"x": f"TXN-{cid}", "t": tid, "ts": now - timedelta(days=9),
             "s": evidence_source})
        db.execute(text(
            "INSERT INTO analytics.fact_case (case_id, tenant_id, opened_ts, state, "
            "severity, fmr_category, amount_paise, recovered_paise, rfa_flag, rail, "
            "region, product, customer_segment, assignee, decision_ts, source) VALUES "
            "(:c, :t, :o, 'fraud_declared', 'critical', 'cheating_and_forgery', 987127, "
            "0, true, 'UPI', 'south', 'savings', 'retail', '', :d, :s)"),
            {"c": cid, "t": tid, "o": now - timedelta(days=10),
             "d": now - timedelta(days=1), "s": source})
        db.execute(text(
            "INSERT INTO analytics.fact_alert (alert_id, tenant_id, txn_id, ts, "
            "rule_family, rule_id, typology, score, severity, disposition, case_id, "
            "analyst, config_version, sub_rule_ref, matched_reason, observed_value, "
            "threshold_value, observed_unit, source) VALUES (:a, :t, :x, :ts, 'LAY', "
            "'LAY-02', 'Layering / mule network', 420, 'critical', 'true_positive', :c, "
            "'', '1.0.0', '.03', 'Fan-in hub', 46, 20, 'distinct_counterparties', :s)"),
            {"a": f"AL-{cid}", "t": tid, "x": f"TXN-{cid}",
             "ts": now - timedelta(days=9), "c": cid, "s": evidence_source})
        db.commit()
        return cid
    finally:
        db.close()


def _drop_case(cid):
    db = SessionLocal()
    try:
        for stmt in ("DELETE FROM analytics.fact_alert WHERE case_id = :c",
                     "DELETE FROM analytics.fact_transaction WHERE txn_id = :x",
                     "DELETE FROM analytics.fact_case WHERE case_id = :c"):
            db.execute(text(stmt), {"c": cid, "x": f"TXN-{cid}"})
        db.commit()
    finally:
        db.close()


def _build(tid, cid):
    from services.analytics_service.app.filings import builder
    db = SessionLocal()
    try:
        return builder.build(db, kind="fmr", tenant_id=tid, case_id=cid,
                             entity={"legal_name": "Test Bank"}, policy={})
    finally:
        db.close()


def test_a_return_builds_from_live_evidence(tid):
    """The control: with live provenance the same call succeeds, so a later failure is
    attributable to provenance and not to the fixture being broken."""
    cid = _seed_case(tid, "live", "live")
    try:
        payload = _build(tid, cid)
        assert payload["case_reference"] == cid
    finally:
        _drop_case(cid)


@pytest.mark.parametrize("case_src,evidence_src,why", [
    ("synthetic", "synthetic", "an entirely demonstration case"),
    ("live", "synthetic", "a case marked live whose transactions are not"),
    ("synthetic", "live", "a demonstration case carrying live evidence"),
])
def test_a_return_refuses_non_live_data(tid, case_src, evidence_src, why):
    """Filing the demo corpus with RBI is a false submission. Prove it cannot happen —
    including the mixed cases, which are the dangerous ones because they look filable."""
    cid = _seed_case(tid, case_src, evidence_src)
    try:
        with pytest.raises(data_source.NotLiveData) as exc:
            _build(tid, cid)
        assert "live traffic only" in str(exc.value), why
    finally:
        _drop_case(cid)


def test_the_refusal_names_what_was_wrong(tid):
    """'Not permitted' leaves the user guessing which of their data is the problem."""
    cid = _seed_case(tid, "synthetic", "synthetic")
    try:
        with pytest.raises(data_source.NotLiveData) as exc:
            _build(tid, cid)
        assert "Demonstration data" in str(exc.value)
        assert exc.value.sources == ["synthetic"]
    finally:
        _drop_case(cid)
