"""Batch file intake.

Every test here corresponds to a way real overnight intake goes wrong: a half-uploaded
file read as complete, a redelivery double-counting value, nine bad rows costing fifty
thousand good ones, and a file whose rail nobody could tell.
"""
import csv
import io
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text

from cp_common.db import SessionLocal
from services.ingestion_service.app import file_intake
from services.ingestion_service.app.file_model import DONE_SUFFIX


@pytest.fixture(autouse=True)
def _clean(tid):
    yield
    db = SessionLocal()
    try:
        db.execute(text("DELETE FROM ingestion.quarantined_rows WHERE tenant_id = :t"),
                   {"t": tid})
        db.execute(text("DELETE FROM ingestion.raw_transaction "
                        "WHERE tenant_id = :t AND source = 'filetest'"), {"t": tid})
        db.execute(text("DELETE FROM ingestion.ingest_files WHERE tenant_id = :t"),
                   {"t": tid})
        db.commit()
    finally:
        db.close()


def _csv(rows, header=("upiTransactionId", "amount", "timestamp", "payerAccount",
                       "payeeAccount", "channel")) -> bytes:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(header)
    for r in rows:
        w.writerow(r)
    return buf.getvalue().encode()


def _row(n, *, amount="1500.50", ts=None):
    ts = ts or (datetime.now(timezone.utc) - timedelta(hours=n)).isoformat()
    return [f"FT-{n}", amount, ts, f"AC-DR-{n}", f"AC-CR-{n}", "mobile"]


def _take(tid, data, filename="UPI_20260803.csv"):
    db = SessionLocal()
    try:
        return file_intake.ingest_bytes(db, tenant_id=tid, filename=filename,
                                        data=data, source="filetest")
    finally:
        db.close()


# ---------------------------------------------------------------- happy path
def test_a_delivered_file_lands_its_rows(tid):
    rep = _take(tid, _csv([_row(i) for i in range(1, 6)]))
    assert rep.state == "completed", rep.error
    assert rep.rows_total == 5
    assert rep.accepted == 5
    assert rep.quarantined == 0


def test_json_lines_are_accepted_too(tid):
    import json
    lines = "\n".join(json.dumps({
        "upiTransactionId": f"JL-{i}", "amount": "900.00",
        "timestamp": (datetime.now(timezone.utc) - timedelta(hours=i)).isoformat(),
        "payerAccount": f"AC-DR-{i}", "payeeAccount": f"AC-CR-{i}",
        "channel": "mobile"}) for i in range(1, 4))
    rep = _take(tid, lines.encode(), filename="UPI_feed.jsonl")
    assert rep.state == "completed" and rep.accepted == 3


# ---------------------------------------------------------------- redelivery
def test_a_byte_identical_redelivery_is_recognised_before_any_row_is_read(tid):
    """Redelivery is a normal operation, not an incident. It must cost nothing."""
    data = _csv([_row(i) for i in range(10, 15)])
    first = _take(tid, data)
    assert first.accepted == 5

    again = _take(tid, data, filename="UPI_20260803_RESEND.csv")
    assert again.state == "duplicate"
    assert again.duplicate_of == first.file_id
    assert "Already ingested" in again.error

    db = SessionLocal()
    try:
        n = db.scalar(text("SELECT COUNT(*) FROM ingestion.raw_transaction "
                           "WHERE tenant_id = :t AND source = 'filetest'")
                      .bindparams(t=tid))
    finally:
        db.close()
    assert n == 5, "a redelivered file double-counted rows"


def test_an_overlapping_file_takes_only_the_new_rows(tid):
    """Not byte-identical, so it is read - and row uniqueness is the backstop."""
    _take(tid, _csv([_row(i) for i in range(20, 25)]))
    rep = _take(tid, _csv([_row(i) for i in range(23, 28)]),
                filename="UPI_20260804.csv")
    assert rep.state == "completed"
    assert rep.duplicates == 2, f"expected 2 overlapping rows, got {rep.duplicates}"
    assert rep.accepted == 3


# ---------------------------------------------------------------- quarantine
def test_bad_rows_are_quarantined_and_the_good_rows_still_land(tid):
    """Nine bad timestamps in a 50,000-row file is 49,991 usable transactions."""
    rows = [_row(i) for i in range(30, 36)]
    rows[2][2] = "not-a-timestamp"
    rows[4][1] = "abc"
    rep = _take(tid, _csv(rows))
    assert rep.state == "completed"
    assert rep.accepted == 4
    assert rep.quarantined == 2

    db = SessionLocal()
    try:
        q = db.execute(text(
            "SELECT line_number, reason FROM ingestion.quarantined_rows "
            "WHERE file_id = :f ORDER BY line_number"), {"f": rep.file_id}).all()
    finally:
        db.close()
    # Line numbers are as the operator will see them when they open the file: header
    # is line 1, so the third data row is line 4.
    assert [r[0] for r in q] == [4, 6]
    assert all(r[1] for r in q), "a quarantined row has no reason"


def test_a_quarantined_row_keeps_what_arrived(tid):
    """So the failure is reproducible rather than merely counted."""
    rows = [_row(40), _row(41)]
    rows[1][2] = "garbage"
    rep = _take(tid, _csv(rows))
    db = SessionLocal()
    try:
        raw = db.execute(text("SELECT raw FROM ingestion.quarantined_rows "
                              "WHERE file_id = :f"), {"f": rep.file_id}).scalar()
    finally:
        db.close()
    assert raw["timestamp"] == "garbage"
    assert raw["upiTransactionId"] == "FT-41"


# ---------------------------------------------------------------- whole-file refusals
def test_a_file_whose_rail_cannot_be_told_is_refused_not_guessed(tid):
    """Guessing would adapt NEFT rows with the UPI mapping and land plausible nonsense."""
    rep = _take(tid, _csv([_row(50)]), filename="nightly_batch.csv")
    assert rep.state == "failed"
    assert "which rail" in rep.error

    db = SessionLocal()
    try:
        n = db.scalar(text("SELECT COUNT(*) FROM ingestion.raw_transaction "
                           "WHERE tenant_id = :t AND source = 'filetest'")
                      .bindparams(t=tid))
    finally:
        db.close()
    assert n == 0


def test_the_rail_is_read_from_the_filename(tid):
    assert file_intake.rail_from_name("UPI_20260803.csv") == "UPI"
    assert file_intake.rail_from_name("2026-08-03_NEFT_batch.csv") == "NEFT"
    assert file_intake.rail_from_name("nightly.csv") == ""


def test_an_empty_file_is_refused(tid):
    rep = _take(tid, b"")
    assert rep.state == "failed" and "empty" in rep.error.lower()


def test_a_semicolon_export_is_refused_rather_than_quarantined_row_by_row(tid):
    """Otherwise every row fails with an unhelpful 'missing field' and the real cause -
    the wrong delimiter - is buried under fifty thousand identical messages."""
    data = b"txnId;amount;timestamp\nFT-1;100;2026-08-03T10:00:00Z\n"
    rep = _take(tid, data)
    assert rep.state == "failed"
    assert "comma-separated" in rep.error


def test_a_file_that_is_not_utf8_is_refused(tid):
    rep = _take(tid, b"\xff\xfe\x00bad bytes")
    assert rep.state == "failed" and "UTF-8" in rep.error


def test_an_oversized_file_is_refused_before_it_is_read(tid):
    big = b"x" * (file_intake.MAX_FILE_BYTES + 1)
    with pytest.raises(file_intake.FileRejected) as exc:
        _take(tid, big)
    assert "full-history dump" in str(exc.value)


# ---------------------------------------------------------------- the watcher contract
def test_a_file_without_its_done_marker_is_left_alone(tmp_path, tid):
    """SFTP writes are not atomic. Reading on sight ingests half a delivery."""
    from scripts.run_file_intake import process_tenant

    tenant_dir = tmp_path / tid
    incoming = tenant_dir / "incoming"
    incoming.mkdir(parents=True)
    f = incoming / "UPI_20260803.csv"
    f.write_bytes(_csv([_row(60)]))

    db = SessionLocal()
    try:
        reports = process_tenant(db, tenant_dir, commit=True)
    finally:
        db.close()
    assert [r["state"] for r in reports] == ["waiting"]
    assert f.exists(), "an unfinished upload was moved"

    # Now the sender finishes.
    f.with_name(f.name + DONE_SUFFIX).write_text("")
    db = SessionLocal()
    try:
        reports = process_tenant(db, tenant_dir, commit=True)
    finally:
        db.close()
    assert reports[0]["state"] == "completed"
    assert reports[0]["accepted"] == 1
    assert not f.exists(), "a handled file was left in incoming"
    assert (tenant_dir / "archive" / f.name).exists()


def test_a_failed_file_is_moved_aside_not_deleted(tmp_path, tid):
    """The delivery is the evidence. Nothing is deleted to signal an outcome."""
    from scripts.run_file_intake import process_tenant

    tenant_dir = tmp_path / tid
    incoming = tenant_dir / "incoming"
    incoming.mkdir(parents=True)
    f = incoming / "mystery.csv"
    f.write_bytes(_csv([_row(70)]))
    f.with_name(f.name + DONE_SUFFIX).write_text("")

    db = SessionLocal()
    try:
        reports = process_tenant(db, tenant_dir, commit=True)
    finally:
        db.close()
    assert reports[0]["state"] == "failed"
    assert (tenant_dir / "failed" / "mystery.csv").exists()


# ---------------------------------------------------------------- reporting
def test_the_file_register_surfaces_partial_landings(ingestion_client, token_for, tid):
    rows = [_row(80), _row(81)]
    rows[1][2] = "bad"
    _take(tid, _csv(rows))
    body = ingestion_client.get(f"/ingest/{tid}/files",
                                headers=token_for("supervisor")).json()
    assert body["count"] >= 1
    assert body["quarantined_rows"] >= 1
    f = body["files"][0]
    assert f["rows_accepted"] == 1 and f["rows_quarantined"] == 1


def test_quarantined_rows_are_retrievable_for_correction(ingestion_client, token_for,
                                                         tid):
    rows = [_row(90)]
    rows[0][1] = "not-money"
    rep = _take(tid, _csv(rows))
    body = ingestion_client.get(f"/ingest/{tid}/files/{rep.file_id}/quarantine",
                                headers=token_for("supervisor")).json()
    assert body["count"] == 1
    assert body["rows"][0]["line_number"] == 2
    assert body["rows"][0]["raw"]["amount"] == "not-money"
