"""Export is a privileged, traceable action - not a convenience."""
import pytest


def _export(client, tid, headers, entity="alert", **params):
    return client.get(f"/analytics/{tid}/export/{entity}", headers=headers, params=params)


def test_export_is_masked_by_default(analytics_client, token_for, tid):
    r = _export(analytics_client, tid, token_for("investigator"), "transaction", limit=20)
    assert r.status_code == 200
    assert "MASKED" in r.text
    assert "•" in r.text, "customer identifiers left the platform in the clear"


def test_export_carries_provenance(analytics_client, token_for, tid):
    """A spreadsheet found later must be traceable back and reproducible."""
    r = _export(analytics_client, tid, token_for("investigator"), "alert",
                limit=10, families="LAY")
    head = r.text.splitlines()[:9]
    joined = "\n".join(head)
    assert "# Fraud360 export" in joined
    assert "exported_by: investigator@test.local" in joined
    assert "exported_at:" in joined
    assert "'families': ['LAY']" in joined, "filters not recorded in the file"
    assert "of" in joined and "rows:" in joined


def test_export_sets_a_download_filename(analytics_client, token_for, tid):
    r = _export(analytics_client, tid, token_for("investigator"), "case", limit=5)
    cd = r.headers.get("content-disposition", "")
    assert "attachment" in cd and cd.endswith('.csv"')
    assert r.headers["content-type"].startswith("text/csv")


def test_export_rows_are_capped(analytics_client, token_for, tid):
    """A bulk extract of an entire tenant is not a reporting feature."""
    r = _export(analytics_client, tid, token_for("investigator"), "transaction",
                limit=999_999)
    assert r.status_code == 422


def test_export_respects_filters(analytics_client, token_for, tid):
    h = token_for("investigator")
    everything = _export(analytics_client, tid, h, "alert", limit=5000).text.count("\n")
    filtered = _export(analytics_client, tid, h, "alert", limit=5000,
                       families="LAY").text.count("\n")
    assert 0 < filtered < everything


def test_unmasked_export_needs_capability_and_reason(analytics_client, token_for, tid):
    # no justification
    assert _export(analytics_client, tid, token_for("investigator"), "transaction",
                   limit=5, reveal="true").status_code == 400
    # role without the capability
    assert _export(analytics_client, tid, token_for("board"), "case",
                   limit=5, reveal="true",
                   justification="board pack preparation").status_code == 403


def test_unmasked_export_is_flagged_in_the_file(analytics_client, token_for, tid):
    r = _export(analytics_client, tid, token_for("investigator"), "transaction",
                limit=5, reveal="true", justification="regulator evidence request")
    assert r.status_code == 200
    assert "UNMASKED" in r.text and "DPDP" in r.text


def test_export_is_audited(analytics_client, token_for, tid):
    from sqlalchemy import select
    from cp_common import AuditLog, SessionLocal

    _export(analytics_client, tid, token_for("supervisor"), "case", limit=5)
    db = SessionLocal()
    try:
        actions = [a for a in db.scalars(
            select(AuditLog).where(AuditLog.tenant_id == tid))
            if a.action == "data.export"]
    finally:
        db.close()
    assert actions, "export was not audited"
    assert "rows" in actions[-1].detail
