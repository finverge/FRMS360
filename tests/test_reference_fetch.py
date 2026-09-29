"""Scheduled reference retrieval (BR-212).

A scheduler that loads whatever it is handed turns a broken feed into silent
non-screening, which is worse than no scheduler at all - an unautomated list at least
visibly goes stale. Most of this file is about what must *not* be activated.
"""
import json
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text

from cp_common.db import SessionLocal
from services.analytics_service.app import reference_fetch as rf
from services.analytics_service.app.detection import reference as ref
from services.analytics_service.app.reference_source_model import ReferenceSource


@pytest.fixture(autouse=True)
def _clean(tid):
    yield
    db = SessionLocal()
    try:
        for stmt in ("DELETE FROM analytics.reference_entries WHERE tenant_id = :t",
                     "DELETE FROM analytics.reference_lists WHERE tenant_id = :t",
                     "DELETE FROM analytics.reference_sources WHERE tenant_id = :t"):
            db.execute(text(stmt), {"t": tid})
        db.commit()
    finally:
        db.close()


def _source(tid, *, kind="sanctions", fmt="lines", cadence=24, max_shrink=0.10):
    db = SessionLocal()
    try:
        s = ReferenceSource(tenant_id=tid, kind=kind,
                            url="https://example.invalid/list.txt", fmt=fmt,
                            cadence_hours=cadence, max_shrink=max_shrink)
        db.add(s)
        db.commit()
        return s.id
    finally:
        db.close()


def _refresh(source_id, body: bytes, now=None):
    db = SessionLocal()
    try:
        src = db.get(ReferenceSource, source_id)
        return rf.refresh(db, src, now=now, fetcher=lambda _s: body)
    finally:
        db.close()


def _names(n, prefix="Designated Person"):
    return "\n".join(f"{prefix} {i}" for i in range(1, n + 1)).encode()


# ------------------------------------------------------------------ parsing
def test_a_line_list_parses_and_ignores_comments():
    entries = rf.parse_document(b"# UNSC consolidated\nAlpha One\n\nBravo Two\n", "lines")
    assert [e["key"] for e in entries] == ["Alpha One", "Bravo Two"]


def test_a_csv_keeps_the_other_columns_as_attributes():
    doc = b"name,reference,dob\nAlpha One,QDi.001,1970-01-01\n"
    entries = rf.parse_document(doc, "csv", key_field="name")
    assert entries[0]["key"] == "Alpha One"
    assert entries[0]["attributes"]["reference"] == "QDi.001"


def test_a_csv_missing_its_key_field_is_refused():
    with pytest.raises(rf.FetchRefused) as exc:
        rf.parse_document(b"a,b\n1,2\n", "csv", key_field="name")
    assert "key field 'name'" in str(exc.value)


def test_json_accepts_a_bare_list_or_an_entries_object():
    assert len(rf.parse_document(b'["Alpha","Bravo"]', "json")) == 2
    doc = json.dumps({"entries": [{"name": "Alpha", "ref": "X"}]}).encode()
    parsed = rf.parse_document(doc, "json")
    assert parsed[0]["key"] == "Alpha" and parsed[0]["attributes"]["ref"] == "X"


def test_a_non_utf8_document_is_refused():
    with pytest.raises(rf.FetchRefused):
        rf.parse_document(b"\xff\xfe\x00bad", "lines")


# ------------------------------------------------------------------ the shrink guard
def test_an_empty_document_is_never_activated():
    """An empty screening list reports clean matches for every counterparty."""
    with pytest.raises(rf.FetchRefused) as exc:
        rf.check_shrinkage(0, 1000, 0.10)
    assert "clean matches" in str(exc.value)


def test_a_list_that_lost_most_of_itself_is_refused():
    """A truncated download, or a login page served with a 200."""
    with pytest.raises(rf.FetchRefused) as exc:
        rf.check_shrinkage(100, 1000, 0.10)
    assert "truncated download" in str(exc.value)


def test_a_normal_republication_passes():
    rf.check_shrinkage(995, 1000, 0.10)     # five designations removed
    rf.check_shrinkage(1200, 1000, 0.10)    # growth is never suspicious


def test_the_first_load_has_nothing_to_compare_against():
    rf.check_shrinkage(50, 0, 0.10)


def test_a_shrunken_fetch_leaves_the_previous_list_active(tid):
    """The property that matters: screening keeps working while the feed is broken."""
    sid = _source(tid)
    first = _refresh(sid, _names(100))
    assert first.outcome == "loaded" and first.entries == 100

    broken = _refresh(sid, _names(5))
    assert broken.outcome == "refused"
    assert "dropped from 100" in broken.detail

    db = SessionLocal()
    try:
        screening = ref.load_screening_set(db, tid)
        active = db.execute(text(
            "SELECT version, entry_count FROM analytics.reference_lists "
            "WHERE tenant_id = :t AND active IS TRUE"), {"t": tid}).first()
    finally:
        db.close()
    assert active[1] == 100, "a truncated feed replaced a working list"
    assert screening["availability"]["sanctions"].loaded is True


def test_a_refusal_is_recorded_against_the_source(tid):
    """A refusal is not an outage, but it must not be silent either."""
    sid = _source(tid)
    _refresh(sid, _names(100))
    _refresh(sid, _names(1))
    db = SessionLocal()
    try:
        src = db.get(ReferenceSource, sid)
        assert src.last_error and "dropped from" in src.last_error
        # The successful load's count is retained, so the next comparison is still right.
        assert src.last_entry_count == 100
    finally:
        db.close()


# ------------------------------------------------------------------ polling
def test_an_unchanged_document_creates_no_new_version(tid):
    """A daily poll of a weekly list must cost one request and no churn in the evidence
    trail."""
    sid = _source(tid)
    body = _names(50)
    assert _refresh(sid, body).outcome == "loaded"
    again = _refresh(sid, body)
    assert again.outcome == "unchanged"

    db = SessionLocal()
    try:
        n = db.scalar(text("SELECT COUNT(*) FROM analytics.reference_lists "
                           "WHERE tenant_id = :t").bindparams(t=tid))
    finally:
        db.close()
    assert n == 1, "an unchanged document created a second version"


def test_a_changed_document_supersedes_and_activates(tid):
    sid = _source(tid)
    _refresh(sid, _names(50))
    res = _refresh(sid, _names(55))
    assert res.outcome == "loaded" and res.entries == 55
    db = SessionLocal()
    try:
        rows = db.execute(text(
            "SELECT version, active, entry_count FROM analytics.reference_lists "
            "WHERE tenant_id = :t ORDER BY loaded_at"), {"t": tid}).all()
    finally:
        db.close()
    assert len(rows) == 2
    assert [r[1] for r in rows] == [False, True], "more than one version is active"


def test_a_transport_failure_is_recorded_not_raised(tid):
    sid = _source(tid)
    db = SessionLocal()
    try:
        src = db.get(ReferenceSource, sid)

        def boom(_s):
            raise ConnectionError("host unreachable")

        res = rf.refresh(db, src, fetcher=boom)
    finally:
        db.close()
    assert res.outcome == "error" and "ConnectionError" in res.detail


def test_the_cadence_governs_when_a_source_is_due(tid):
    sid = _source(tid, cadence=24)
    now = datetime.now(timezone.utc)
    _refresh(sid, _names(10), now=now)
    db = SessionLocal()
    try:
        src = db.get(ReferenceSource, sid)
        assert rf.is_due(src, now) is False
        assert rf.is_due(src, now + timedelta(hours=25)) is True
    finally:
        db.close()


# ------------------------------------------------------------------ staleness
def test_staleness_reports_kinds_with_no_feed_at_all(tid):
    """"Nobody set one up" and "the feed is broken" both end in screening against an old
    list, so both are reported."""
    db = SessionLocal()
    try:
        rows = {r["kind"]: r for r in rf.staleness(db, tid)}
    finally:
        db.close()
    assert rows["sanctions"]["loaded"] is False
    assert rows["sanctions"]["automated"] is False
    assert "CPT-02" in rows["sanctions"]["feeds"]


def test_staleness_shows_age_and_the_last_error(tid):
    sid = _source(tid)
    _refresh(sid, _names(100))
    _refresh(sid, _names(2))          # refused
    db = SessionLocal()
    try:
        rows = {r["kind"]: r for r in rf.staleness(db, tid)}
    finally:
        db.close()
    s = rows["sanctions"]
    assert s["loaded"] is True and s["entry_count"] == 100
    assert s["automated"] is True
    assert s["age_hours"] is not None and s["age_hours"] >= 0
    assert "dropped from" in s["last_error"]


def test_the_source_credential_is_sealed_at_rest(tid):
    """A feed token is a credential like any other."""
    db = SessionLocal()
    try:
        s = ReferenceSource(tenant_id=tid, kind="pep",
                            url="https://example.invalid/pep.json", fmt="json",
                            auth_header="Authorization", auth_value="Bearer s3cret")
        db.add(s)
        db.commit()
        raw = db.execute(text("SELECT auth_value FROM analytics.reference_sources "
                              "WHERE id = :i"), {"i": s.id}).scalar()
    finally:
        db.close()
    assert "s3cret" not in raw
    assert raw.startswith("enc:v")
