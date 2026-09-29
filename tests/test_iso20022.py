"""ISO 20022 credit-transfer intake (BR-209).

The group header of an ISO message states how many transactions it holds and what they
total. Most implementations ignore both, which is how a truncated payment file lands as a
short but entirely plausible batch. Half these tests exist to hold that line.
"""
from datetime import datetime, timezone

import pytest
from sqlalchemy import text

from cp_common.db import SessionLocal
from services.ingestion_service.app import file_intake, iso20022
from services.ingestion_service.app.adapters import AdapterError

NS8 = "urn:iso:std:iso:20022:tech:xsd:pacs.008.001.08"


def _tx(n, amount="1500.50", ccy="INR"):
    return f"""
  <CdtTrfTxInf>
    <PmtId><InstrId>INSTR-{n}</InstrId><EndToEndId>E2E-{n}</EndToEndId>
      <TxId>ISO-TX-{n}</TxId></PmtId>
    <IntrBkSttlmAmt Ccy="{ccy}">{amount}</IntrBkSttlmAmt>
    <IntrBkSttlmDt>2026-08-01</IntrBkSttlmDt>
    <Dbtr><Nm>Ravi Traders</Nm></Dbtr>
    <DbtrAcct><Id><Othr><Id>AC-ISO-DR-{n}</Id></Othr></Id></DbtrAcct>
    <Cdtr><Nm>Suresh Supplies</Nm></Cdtr>
    <CdtrAcct><Id><Othr><Id>AC-ISO-CR-{n}</Id></Othr></Id></CdtrAcct>
    <RmtInf><Ustrd>Invoice 8842</Ustrd></RmtInf>
  </CdtTrfTxInf>"""


def pacs008(count=2, *, declared=None, ctrl_sum=None, amount="1500.50", ccy="INR",
            ns=NS8) -> bytes:
    txs = "".join(_tx(i, amount, ccy) for i in range(1, count + 1))
    n = count if declared is None else declared
    ctrl = "" if ctrl_sum is None else f"<CtrlSum>{ctrl_sum}</CtrlSum>"
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<Document xmlns="{ns}">
 <FIToFICstmrCdtTrf>
  <GrpHdr><MsgId>MSG-001</MsgId><CreDtTm>2026-08-01T02:00:00Z</CreDtTm>
    <NbOfTxs>{n}</NbOfTxs>{ctrl}</GrpHdr>
  {txs}
 </FIToFICstmrCdtTrf>
</Document>""".encode()


# ------------------------------------------------------------------ parsing
def test_a_pacs008_parses_into_transaction_rows():
    rows, header = iso20022.parse(pacs008(2))
    assert header["message_type"] == "pacs.008"
    assert header["parsed_count"] == 2
    assert rows[0]["txn_id"] == "ISO-TX-1"
    assert rows[0]["debtor_account"] == "AC-ISO-DR-1"
    assert rows[0]["creditor_account"] == "AC-ISO-CR-1"
    assert rows[0]["amount"] == "1500.50"
    assert rows[0]["remittance_info"] == "Invoice 8842"


def test_the_namespace_version_does_not_have_to_match():
    """pacs.008.001.08, .09, .10 - pinning one means a bank's upgrade silently stops the
    feed. Structure is stable across versions; the URN is not."""
    rows, header = iso20022.parse(
        pacs008(1, ns="urn:iso:std:iso:20022:tech:xsd:pacs.008.001.10"))
    assert header["message_type"] == "pacs.008" and len(rows) == 1


def test_an_iban_account_is_accepted_too():
    xml = pacs008(1).replace(b"<Othr><Id>AC-ISO-DR-1</Id></Othr>",
                             b"<IBAN>DE89370400440532013000</IBAN>")
    rows, _ = iso20022.parse(xml)
    assert rows[0]["debtor_account"] == "DE89370400440532013000"


# ------------------------------------------------------------------ the group header
def test_a_truncated_file_is_refused_not_partially_ingested():
    """The failure this check exists for: a file that half-lands looks fine afterwards."""
    with pytest.raises(AdapterError) as exc:
        iso20022.parse(pacs008(2, declared=5))
    assert "declares 5" in str(exc.value) and "contains 2" in str(exc.value)


def test_a_control_sum_mismatch_is_refused():
    with pytest.raises(AdapterError) as exc:
        iso20022.parse(pacs008(2, amount="100.00", ctrl_sum="999.00"))
    assert "control sum" in str(exc.value)


def test_a_matching_control_sum_passes():
    rows, header = iso20022.parse(pacs008(2, amount="100.00", ctrl_sum="200.00"))
    assert len(rows) == 2 and header["total"] == "200.00"


def test_a_message_without_a_control_sum_is_still_accepted():
    """CtrlSum is optional in the standard; NbOfTxs alone must not be treated as absent."""
    rows, header = iso20022.parse(pacs008(3))
    assert len(rows) == 3 and header["control_sum"] == ""


# ------------------------------------------------------------------ refusals
def test_a_foreign_currency_is_refused_rather_than_converted():
    """to_paise on a USD amount produces a number wrong by the exchange rate and
    indistinguishable from a correct one."""
    with pytest.raises(AdapterError) as exc:
        iso20022.parse(pacs008(1, ccy="USD"))
    assert "USD" in str(exc.value) and "will not convert" in str(exc.value)


def test_a_transaction_without_an_identifier_is_refused():
    xml = pacs008(1).replace(b"<TxId>ISO-TX-1</TxId>", b"") \
                    .replace(b"<EndToEndId>E2E-1</EndToEndId>", b"") \
                    .replace(b"<InstrId>INSTR-1</InstrId>", b"")
    with pytest.raises(AdapterError) as exc:
        iso20022.parse(xml)
    assert "identifier" in str(exc.value)


def test_a_transaction_missing_an_account_is_refused():
    xml = pacs008(1).replace(
        b"<CdtrAcct><Id><Othr><Id>AC-ISO-CR-1</Id></Othr></Id></CdtrAcct>", b"")
    with pytest.raises(AdapterError) as exc:
        iso20022.parse(xml)
    assert "debtor or creditor account" in str(exc.value)


def test_a_dtd_is_refused():
    """Billion laughs, in a file a bank drops on an SFTP landing zone."""
    evil = (b'<?xml version="1.0"?><!DOCTYPE r [<!ENTITY x "boom">]>'
            b'<Document xmlns="' + NS8.encode() + b'"><FIToFICstmrCdtTrf/></Document>')
    with pytest.raises(AdapterError):
        iso20022.parse(evil)


def test_a_non_iso_xml_is_refused():
    with pytest.raises(AdapterError) as exc:
        iso20022.parse(b"<Document><Something/></Document>")
    assert "not a recognised ISO 20022" in str(exc.value)


# ------------------------------------------------------------------ through file intake
@pytest.fixture(autouse=True)
def _clean(tid):
    yield
    db = SessionLocal()
    try:
        db.execute(text("DELETE FROM ingestion.raw_transaction "
                        "WHERE tenant_id = :t AND source = 'isotest'"), {"t": tid})
        db.execute(text("DELETE FROM ingestion.quarantined_rows WHERE tenant_id = :t"),
                   {"t": tid})
        db.execute(text("DELETE FROM ingestion.ingest_files WHERE tenant_id = :t"),
                   {"t": tid})
        db.commit()
    finally:
        db.close()


def _take(tid, data, filename="pacs008_20260803.xml"):
    db = SessionLocal()
    try:
        return file_intake.ingest_bytes(db, tenant_id=tid, filename=filename,
                                        data=data, source="isotest")
    finally:
        db.close()


def test_an_iso_file_lands_through_the_normal_intake_path(tid):
    rep = _take(tid, pacs008(3))
    assert rep.state == "completed", rep.error
    assert rep.accepted == 3
    assert rep.quarantined == 0


def test_an_iso_file_needs_no_rail_in_its_name(tid):
    """A CSV without a rail is refused; an ISO message does not name one at all, so the
    documented default applies and is recorded."""
    rep = _take(tid, pacs008(1), filename="nightly_iso_batch.xml")
    assert rep.state == "completed", rep.error
    db = SessionLocal()
    try:
        rail = db.execute(text("SELECT rail FROM ingestion.ingest_files WHERE id = :i"),
                          {"i": rep.file_id}).scalar()
    finally:
        db.close()
    assert rail == iso20022.DEFAULT_RAIL


def test_a_corrupt_iso_file_fails_whole_rather_than_quarantining_rows(tid):
    """There is nothing to quarantine: the header says the file is not what arrived."""
    rep = _take(tid, pacs008(2, declared=9))
    assert rep.state == "failed"
    assert "declares 9" in rep.error
    assert rep.accepted == 0 and rep.quarantined == 0

    db = SessionLocal()
    try:
        n = db.scalar(text("SELECT COUNT(*) FROM ingestion.raw_transaction "
                           "WHERE tenant_id = :t AND source = 'isotest'")
                      .bindparams(t=tid))
    finally:
        db.close()
    assert n == 0, "rows from a corrupt ISO file were ingested"


def test_a_redelivered_iso_file_is_recognised(tid):
    data = pacs008(2)
    assert _take(tid, data).accepted == 2
    again = _take(tid, data, filename="pacs008_RESEND.xml")
    assert again.state == "duplicate"
