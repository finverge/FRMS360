"""Synthetic ISO 20022 traffic through the real pipeline: XML -> file_intake.ingest_bytes
(the same intake path a bank's SFTP delivery uses) -> the real detection engine
(run_until_empty) -> a real alert.

Mirrors test_detection.py's test_a_mule_ring_is_detected_end_to_end exactly, with one
difference: the batch arrives as an ISO 20022 pacs.008 file instead of the JSON REST
batch. If both tests pass, the fan-in typology is detected identically regardless of
which wire format it arrived in - the point of adapting every rail to one canonical shape
before detection ever sees it (BR-202).
"""
from datetime import datetime, timezone

import pytest
from sqlalchemy import text

from cp_common.db import SessionLocal
from services.analytics_service.app.detection import run_until_empty
from services.ingestion_service.app import file_intake

NS8 = "urn:iso:std:iso:20022:tech:xsd:pacs.008.001.08"
HUB = "AC-ISO-DET-HUB-1"
SOURCE = "iso20022e2e"  # ingestion.ingest_files.source is varchar(16)


def _fan_in_tx(n: int, hub: str, amount: str, settlement_date: str) -> str:
    return f"""
  <CdtTrfTxInf>
    <PmtId><InstrId>ISOFAN-{n:04d}</InstrId><EndToEndId>ISOFAN-E2E-{n:04d}</EndToEndId>
      <TxId>ISOFAN-TX-{n:04d}</TxId></PmtId>
    <IntrBkSttlmAmt Ccy="INR">{amount}</IntrBkSttlmAmt>
    <IntrBkSttlmDt>{settlement_date}</IntrBkSttlmDt>
    <Dbtr><Nm>Payer {n}</Nm></Dbtr>
    <DbtrAcct><Id><Othr><Id>AC-ISO-DET-P{n:03d}</Id></Othr></Id></DbtrAcct>
    <Cdtr><Nm>Collection Hub</Nm></Cdtr>
    <CdtrAcct><Id><Othr><Id>{hub}</Id></Othr></Id></CdtrAcct>
    <RmtInf><Ustrd>Synthetic fan-in test</Ustrd></RmtInf>
  </CdtTrfTxInf>"""


def _fan_in_pacs008(count: int, hub: str = HUB, amount: str = "24000.00") -> bytes:
    """A fan-in mule pattern (many distinct payers, one hub) rendered as a single
    pacs.008 FI-to-FI credit transfer file, correct group header included - see
    iso20022.py's docstring on why NbOfTxs/CtrlSum are verified rather than trusted."""
    today = datetime.now(timezone.utc).date().isoformat()
    txs = "".join(_fan_in_tx(i, hub, amount, today) for i in range(1, count + 1))
    total = f"{float(amount) * count:.2f}"
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<Document xmlns="{NS8}">
 <FIToFICstmrCdtTrf>
  <GrpHdr><MsgId>ISOFAN-MSG-001</MsgId><CreDtTm>{today}T02:00:00Z</CreDtTm>
    <NbOfTxs>{count}</NbOfTxs><CtrlSum>{total}</CtrlSum></GrpHdr>
  {txs}
 </FIToFICstmrCdtTrf>
</Document>""".encode()


@pytest.fixture
def clean_slate(tid):
    """Detection and file-intake both write real facts - clean up after this file the
    same way test_detection.py's fixture of the same name does, scoped to this file's
    own tags so nothing outside it is touched."""
    yield
    db = SessionLocal()
    try:
        db.execute(text(
            "DELETE FROM analytics.fact_alert WHERE tenant_id = :t "
            "AND txn_id LIKE 'ISOFAN-%'"), {"t": tid})
        db.execute(text(
            "DELETE FROM analytics.fact_transaction WHERE tenant_id = :t "
            "AND txn_id LIKE 'ISOFAN-%'"), {"t": tid})
        db.execute(text(
            "DELETE FROM analytics.fact_case WHERE tenant_id = :t AND case_id IN ("
            "  SELECT case_id FROM analytics.fact_case c WHERE c.tenant_id = :t"
            "    AND NOT EXISTS (SELECT 1 FROM analytics.fact_alert a "
            "                    WHERE a.case_id = c.case_id))"), {"t": tid})
        db.execute(text(
            "DELETE FROM ingestion.raw_transaction WHERE tenant_id = :t "
            "AND source = :s"), {"t": tid, "s": SOURCE})
        db.execute(text(
            "DELETE FROM ingestion.quarantined_rows WHERE tenant_id = :t"), {"t": tid})
        db.execute(text(
            "DELETE FROM ingestion.ingest_files WHERE tenant_id = :t "
            "AND filename LIKE 'isofan_%'"), {"t": tid})
        db.commit()
    finally:
        db.close()


def _ingest(tid, data: bytes, filename: str = "isofan_20260817.xml"):
    db = SessionLocal()
    try:
        return file_intake.ingest_bytes(db, tenant_id=tid, filename=filename, data=data,
                                        source=SOURCE)
    finally:
        db.close()


def _run(tid):
    db = SessionLocal()
    try:
        return run_until_empty(db, tid, batch=500)
    finally:
        db.close()


# ------------------------------------------------------------------ the pipeline itself
def test_a_pacs008_file_lands_through_the_normal_intake_path(tid, clean_slate):
    rep = _ingest(tid, _fan_in_pacs008(3))
    assert rep.state == "completed", rep.error
    assert rep.accepted == 3
    assert rep.quarantined == 0


def test_a_truncated_pacs008_fails_the_whole_file_not_a_partial_batch(tid, clean_slate):
    """The control-sum check this file's other tests rely on being correct - proven
    here by breaking it and confirming file_intake refuses the delivery rather than
    quietly accepting a short batch."""
    good = _fan_in_pacs008(5)
    tampered = good.replace(b"<NbOfTxs>5</NbOfTxs>", b"<NbOfTxs>7</NbOfTxs>")
    rep = _ingest(tid, tampered, filename="isofan_tampered.xml")
    assert rep.state == "failed"
    assert "declares" in rep.error.lower() and "truncated" in rep.error.lower(), rep.error


# ------------------------------------------------------------------ the rule engine
def test_a_fan_in_mule_ring_synthesised_as_iso20022_is_detected_end_to_end(tid, clean_slate):
    """The same LAY-02 pattern test_detection.py proves over the JSON REST batch
    (test_a_mule_ring_is_detected_end_to_end), synthesised here as an ISO 20022 file
    instead - proving the file-intake path reaches the identical detection outcome."""
    rep = _ingest(tid, _fan_in_pacs008(46))
    assert rep.state == "completed", rep.error
    assert rep.accepted == 46

    run = _run(tid)
    # run_until_empty drains this tenant's *whole* pending queue, not only this test's
    # rows - other suites ingest against the same shared tid without necessarily
    # draining it themselves first (test_detection.py's own fan-in tests happen to run
    # before any such leftovers accumulate; this one does not get that guarantee in a
    # full-suite run). So the assertion is on this batch's own hub, which nothing else
    # in the suite writes to, not on the aggregate totals across the whole queue.
    assert run.projected >= 46, f"46 accepted, only {run.projected} projected"
    assert run.alerts > 0, "a 46-payer ISO 20022 fan-in produced no alerts at all"
    assert run.fired_rules.get("LAY-02") == 46, (
        f"expected LAY-02 to fire exactly once per synthetic payer (46); "
        f"fired: {run.fired_rules}")


def test_a_small_iso20022_batch_stays_under_the_fan_in_threshold(tid, clean_slate):
    """The negative case: fewer distinct payers than LAY-02's threshold must not fire it -
    otherwise the positive test above would be proving nothing about the threshold."""
    rep = _ingest(tid, _fan_in_pacs008(5, hub="AC-ISO-DET-HUB-SMALL"),
                 filename="isofan_small.xml")
    assert rep.state == "completed", rep.error

    run = _run(tid)
    assert "LAY-02" not in run.fired_rules, \
        "5 distinct payers fired the 40+ fan-in rule - the threshold is not being honoured"
