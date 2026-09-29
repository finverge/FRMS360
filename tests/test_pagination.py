"""Drill pagination.

The drill views were capped at 40 rows with no total, so the first page silently looked
like the whole result set - unusable at real volumes and quietly misleading.
"""
import pytest

ENTITIES = ["alert", "case", "transaction"]


def _drill(client, tid, headers, entity, **params):
    r = client.get(f"/analytics/{tid}/drill/{entity}", headers=headers, params=params)
    assert r.status_code == 200, r.text
    return r.json()


@pytest.mark.parametrize("entity", ENTITIES)
def test_drill_reports_a_total_beyond_the_page(analytics_client, token_for, tid, entity):
    d = _drill(analytics_client, tid, token_for("investigator"), entity, limit=5)
    assert d["total"] >= d["count"], "total must count the whole result set"
    assert d["limit"] == 5 and d["offset"] == 0
    assert isinstance(d["has_more"], bool)


def test_paging_walks_the_result_set_without_gaps_or_repeats(analytics_client, token_for, tid):
    h = token_for("investigator")
    ids, offset, guard = [], 0, 0
    while guard < 40:
        d = _drill(analytics_client, tid, h, "alert", limit=25, offset=offset)
        ids.extend(r["alert_id"] for r in d["rows"])
        if not d["has_more"]:
            break
        offset += 25
        guard += 1
    total = _drill(analytics_client, tid, h, "alert", limit=1)["total"]
    assert len(ids) == total, f"walked {len(ids)} rows but total says {total}"
    assert len(set(ids)) == len(ids), "a row appeared on two pages"


def test_offset_past_the_end_returns_empty_not_an_error(analytics_client, token_for, tid):
    h = token_for("investigator")
    total = _drill(analytics_client, tid, h, "case", limit=1)["total"]
    d = _drill(analytics_client, tid, h, "case", limit=25, offset=total + 500)
    assert d["rows"] == [] and d["has_more"] is False


def test_filters_change_the_total(analytics_client, token_for, tid):
    """The count must respect filters, or the pager lies about how much is there."""
    h = token_for("investigator")
    everything = _drill(analytics_client, tid, h, "alert", limit=1)["total"]
    filtered = _drill(analytics_client, tid, h, "alert", limit=1, families="LAY")["total"]
    assert 0 < filtered < everything


def test_page_size_is_capped(analytics_client, token_for, tid):
    """An unbounded page size is a denial-of-service on our own database."""
    r = analytics_client.get(f"/analytics/{tid}/drill/alert",
                             headers=token_for("investigator"), params={"limit": 100000})
    assert r.status_code == 422


def test_paged_rows_stay_masked(analytics_client, token_for, tid):
    """Masking must not be lost on later pages."""
    d = _drill(analytics_client, tid, token_for("investigator"), "transaction",
               limit=10, offset=10)
    assert d["pii_masked"] is True
    for row in d["rows"]:
        assert "•" in str(row["debtor_account"])
