"""Lane C's own reference-feed mechanism (mca_roc, rating_action) - mirrors
tests/test_reference_fetch.py's stub-fetcher-via-fetcher= pattern exactly, no real HTTP.
Most of this file is about what must NOT be activated, same as the Lane B version.
"""
import json

import pytest
from sqlalchemy import text

from cp_common.db import SessionLocal
from services.lane_c_service.app import reference_fetch as rf
from services.lane_c_service.app.reference_source_model import LaneCReferenceSource


@pytest.fixture(autouse=True)
def _clean(tid):
    yield
    db = SessionLocal()
    try:
        for stmt in ("DELETE FROM lane_c.reference_entries WHERE tenant_id = :t",
                     "DELETE FROM lane_c.reference_lists WHERE tenant_id = :t",
                     "DELETE FROM lane_c.reference_sources WHERE tenant_id = :t"):
            db.execute(text(stmt), {"t": tid})
        db.commit()
    finally:
        db.close()


def _source(tid, *, kind="mca_roc", fmt="json", cadence=24, max_shrink=0.10):
    db = SessionLocal()
    try:
        s = LaneCReferenceSource(tenant_id=tid, kind=kind,
                                 url="https://example.invalid/list.json", fmt=fmt,
                                 key_field="cin", cadence_hours=cadence,
                                 max_shrink=max_shrink)
        db.add(s)
        db.commit()
        return s.id
    finally:
        db.close()


def _refresh(source_id, body: bytes, now=None):
    db = SessionLocal()
    try:
        src = db.get(LaneCReferenceSource, source_id)
        return rf.refresh(db, src, now=now, fetcher=lambda _s: body)
    finally:
        db.close()


def _roc_body(entries: list[dict]):
    return json.dumps({"entries": entries}).encode()


# ------------------------------------------------------------------ parsing
def test_a_json_entry_keeps_attributes_under_the_key_field():
    doc = _roc_body([{"cin": "U12345MH2020PTC000001", "has_undisclosed_liability": True,
                      "detail": "Unpaid charge with a private lender."}])
    entries = rf.parse_document(doc, "json", key_field="cin")
    assert entries[0]["key"] == "U12345MH2020PTC000001"
    assert entries[0]["attributes"]["has_undisclosed_liability"] is True


def test_an_empty_document_is_refused():
    with pytest.raises(rf.FetchRefused):
        rf.parse_document(_roc_body([]), "json", key_field="cin")


# ------------------------------------------------------------------ refresh / activation
def test_first_load_activates_regardless_of_size(tid):
    sid = _source(tid, kind="mca_roc")
    body = _roc_body([{"cin": "U11111MH2020PTC000001", "has_undisclosed_liability": False}])
    res = _refresh(sid, body)
    assert res.outcome == "loaded"
    assert res.entries == 1


def test_an_unchanged_document_creates_no_new_version(tid):
    sid = _source(tid, kind="mca_roc")
    body = _roc_body([{"cin": "U11111MH2020PTC000001", "has_undisclosed_liability": False}])
    _refresh(sid, body)
    res = _refresh(sid, body)
    assert res.outcome == "unchanged"


def test_a_shrunk_list_is_refused_and_the_prior_version_stays_active(tid):
    sid = _source(tid, kind="mca_roc", max_shrink=0.10)
    big = _roc_body([{"cin": f"U{i:05d}MH2020PTC000001", "has_undisclosed_liability": False}
                     for i in range(20)])
    _refresh(sid, big)

    small = _roc_body([{"cin": "U00001MH2020PTC000001", "has_undisclosed_liability": False}])
    res = _refresh(sid, small)
    assert res.outcome == "refused"

    db = SessionLocal()
    try:
        entry = rf.active_entry(db, tid, "mca_roc", "U00005MH2020PTC000001")
        assert entry is not None, "the prior (larger) version must still be active"
    finally:
        db.close()


def test_active_entry_normalises_the_lookup_key(tid):
    sid = _source(tid, kind="mca_roc")
    body = _roc_body([{"cin": "u-12345 mh2020ptc000001", "has_undisclosed_liability": True,
                       "detail": "test"}])
    _refresh(sid, body)

    db = SessionLocal()
    try:
        entry = rf.active_entry(db, tid, "mca_roc", "U12345MH2020PTC000001")
        assert entry is not None
        assert entry["attributes"]["has_undisclosed_liability"] is True
    finally:
        db.close()


def test_active_entry_is_none_when_no_list_is_loaded(tid):
    db = SessionLocal()
    try:
        assert rf.active_entry(db, tid, "mca_roc", "ANYTHING") is None
    finally:
        db.close()


def test_active_entry_is_none_for_a_key_not_in_the_list(tid):
    sid = _source(tid, kind="mca_roc")
    _refresh(sid, _roc_body([{"cin": "U11111MH2020PTC000001",
                              "has_undisclosed_liability": False}]))
    db = SessionLocal()
    try:
        assert rf.active_entry(db, tid, "mca_roc", "U99999MH2020PTC000009") is None
    finally:
        db.close()
