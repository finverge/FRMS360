"""BR-107: suspension that suspends, and an exit bundle that is actually the record.

Two failures were hiding here, and both are of the same kind — a control that reads as
present and does nothing:

* **Suspension stopped the console login only.** The bank's staff were locked out while
  its payment switch kept posting and its channel kept asking for inline decisions. The
  machine path is where the volume is, so this was the wrong half.
* **The exit bundle swallowed its own failures.** A branding or config fetch that failed
  returned ``None``/``[]`` and the bundle looked complete. On an exit export that is the
  worst place for a silent gap: the bank finds out after the tenant is gone.

So most of what follows is refusal tests. A lifecycle control proves itself by stopping
something, and an export proves itself by admitting what it could not collect.
"""
import pytest

from cp_common import tenant_status as ts


@pytest.fixture(autouse=True)
def _clear_cache():
    ts.forget()
    yield
    ts.forget()


def _stub(monkeypatch, status=None, fail=False, is_sandbox=False):
    def fake(tenant_id):
        if fail:
            raise RuntimeError("tenant-service unreachable")
        return status, is_sandbox
    monkeypatch.setattr(ts, "_fetch", fake)


# ------------------------------------------------------------------ who is served
@pytest.mark.parametrize("status", ["active", "provisioning", "degraded"])
def test_a_working_tenant_is_served(monkeypatch, status):
    """``degraded`` means we are having trouble, not that the tenant did anything."""
    _stub(monkeypatch, status)
    assert ts.standing("t1").served


@pytest.mark.parametrize("status", sorted(ts.NOT_SERVED))
def test_a_suspended_or_offboarded_tenant_is_not_served(monkeypatch, status):
    _stub(monkeypatch, status)
    st = ts.standing("t1")
    assert not st.served
    assert st.reason, "a refusal must say why"


def test_the_refusal_says_the_record_is_still_exportable(monkeypatch):
    """The bank's first question on being suspended is whether its data is gone."""
    _stub(monkeypatch, "suspended")
    assert "exportable" in ts.standing("t1").reason


def test_require_served_raises_with_a_usable_code(monkeypatch):
    from cp_common.errors import AppError
    _stub(monkeypatch, "suspended")
    with pytest.raises(AppError) as exc:
        ts.require_served("t1")
    assert exc.value.status_code == 403
    assert exc.value.code == "tenant_suspended"


# ------------------------------------------------------------------- the cache
def test_the_status_is_cached_rather_than_fetched_per_transaction(monkeypatch):
    """A switch calls intake thousands of times a second; a hop per call is not viable."""
    calls = []
    monkeypatch.setattr(ts, "_fetch", lambda t: (calls.append(t), ("active", False))[1])
    for _ in range(50):
        ts.standing("t1")
    assert len(calls) == 1


def test_the_cache_expires_so_a_suspension_takes_effect(monkeypatch):
    calls = []
    monkeypatch.setattr(
        ts, "_fetch",
        lambda t: (calls.append(t), ("active" if len(calls) < 2 else "suspended", False))[1])
    assert ts.standing("t1", now=0.0).served
    assert not ts.standing("t1", now=ts.TTL_SECONDS + 1).served


def test_forget_makes_a_resume_take_effect_at_once(monkeypatch):
    seq = iter([("suspended", False), ("active", False)])
    monkeypatch.setattr(ts, "_fetch", lambda t: next(seq))
    assert not ts.standing("t1").served
    ts.forget("t1")
    assert ts.standing("t1").served


# ---------------------------------------------------- when the lookup itself fails
def test_a_lookup_failure_falls_back_to_the_last_answer_and_marks_it_stale(monkeypatch):
    seq = iter([("suspended", False)])
    monkeypatch.setattr(ts, "_fetch", lambda t: next(seq))
    ts.standing("t1", now=0.0)
    _stub(monkeypatch, fail=True)
    st = ts.standing("t1", now=ts.TTL_SECONDS + 1)
    assert not st.served and st.stale
    assert "cached answer" in st.reason


def test_an_unknown_tenant_with_no_answer_is_served_and_says_so(monkeypatch):
    """The one deliberate fail-open. A tenant-service outage must not become a payments
    outage, and the choice is stated on the result rather than assumed."""
    _stub(monkeypatch, fail=True)
    st = ts.standing("never-seen")
    assert st.served and st.stale and st.status == "unknown"
    assert "could not be checked" in st.reason


# -------------------------------------------- the gate the machine path goes through
def test_a_machine_token_for_a_suspended_tenant_is_refused(monkeypatch):
    """The gate lives on require_machine_scope, so intake and decisioning both get it."""
    from fastapi import HTTPException

    from cp_common.auth import require_machine_scope
    from cp_common.security import create_access_token

    _stub(monkeypatch, "suspended")
    token = create_access_token(subject="svc_x", role="service", tenant_id="t1",
                                scope="ingest")
    with pytest.raises(HTTPException) as exc:
        require_machine_scope("ingest")(f"Bearer {token}")
    assert exc.value.status_code == 403
    assert "suspended" in exc.value.detail


def test_a_machine_token_for_an_active_tenant_still_works(monkeypatch):
    from cp_common.auth import require_machine_scope
    from cp_common.security import create_access_token

    _stub(monkeypatch, "active")
    token = create_access_token(subject="svc_x", role="service", tenant_id="t1",
                                scope="ingest")
    assert require_machine_scope("ingest")(f"Bearer {token}").subject == "svc_x"


def test_a_human_is_not_gated_by_the_machine_lifecycle_check(monkeypatch):
    """Login already refuses suspended tenants. An analyst who is somehow still holding a
    token must not hit a second, differently-worded refusal here — and more importantly,
    this check must not become the thing that decides human access."""
    from cp_common.auth import require_machine_scope
    from cp_common.security import create_access_token

    def explode(t):
        raise AssertionError("a human must not trigger a tenant-status lookup")
    monkeypatch.setattr(ts, "_fetch", explode)
    token = create_access_token(subject="a@b.c", role="analyst", tenant_id="t1",
                                scope="full")
    assert require_machine_scope("ingest")(f"Bearer {token}").role == "analyst"


# ------------------------------------------------------------------ the exit bundle
FRAUD_SECTIONS = {
    "cases", "alerts", "evidence_transactions", "case_transitions",
    "documents_manifest", "filings", "staff_accountability",
    "accountability_findings", "lea_referrals", "recovery_entries",
}


def test_the_fraud_record_covers_every_section_a_bank_must_retain():
    """Named explicitly, so removing one is a test change and not an oversight."""
    import inspect

    from services.analytics_service.app.routes import exit_bundle
    src = inspect.getsource(exit_bundle.export_fraud_record)
    for name in FRAUD_SECTIONS:
        assert f'section("{name}"' in src, f"{name} is not in the exit bundle"


def test_the_document_manifest_carries_hashes_but_not_bytes():
    """Hashes let the bank verify what it downloads. Megabytes of PDF inside a JSON
    payload would make the bundle unusable in the name of completeness."""
    import inspect

    from services.analytics_service.app.routes import exit_bundle
    src = inspect.getsource(exit_bundle.export_fraud_record)
    manifest = src[src.index('section("documents_manifest"'):]
    manifest = manifest[:manifest.index('section("filings"')]
    cols = {c.strip() for c in
            manifest[manifest.index("SELECT") + 6:manifest.index("FROM")].split(",")}
    assert "sha256" in cols
    assert "content" not in cols, "the document bytes must not be in the manifest"
    assert "content_type" in cols, "but the type is metadata and belongs here"


def test_a_failed_section_marks_the_bundle_incomplete_rather_than_empty(monkeypatch):
    """The whole point. A caller must be able to tell a partial bundle from a whole one
    without inspecting every section."""
    from services.analytics_service.app.routes import exit_bundle

    class Boom:
        def execute(self, *a, **k):
            raise RuntimeError("relation does not exist")

    out = exit_bundle.export_fraud_record("t1", db=Boom())
    assert out["complete"] is False
    assert out["problems"]
    assert out["sections"]["cases"] is None, "a failed section must be null, not []"


def test_every_bind_parameter_in_the_bundle_is_actually_supplied():
    """A regression. The ``not_included`` counts referenced ``:t`` and never passed it, so
    that one section failed on every export. It was caught only because the bundle now
    reports its own failures — which is the argument for the completeness flag in one
    line, but it should not need an end-to-end run to find."""
    import re

    from sqlalchemy import text as sqltext

    from services.analytics_service.app.routes import exit_bundle

    unbound = []

    class Recorder:
        def execute(self, clause, params=None):
            sql = str(clause)
            needed = set(re.findall(r"(?<![:\w]):(\w+)", sql))
            missing = needed - set((params or {}))
            if missing:
                unbound.append((sql.strip().split("\n")[0][:60], sorted(missing)))
            raise RuntimeError("stop here; we only wanted the parameters")

    exit_bundle.export_fraud_record("t1", db=Recorder())
    assert not unbound, f"SQL with unsupplied bind parameters: {unbound}"
    assert sqltext  # the import documents what `clause` is


def test_what_is_left_out_is_counted_rather_than_dropped():
    import inspect

    from services.analytics_service.app.routes import exit_bundle
    src = inspect.getsource(exit_bundle.export_fraud_record)
    assert "not_included" in src and "bulk_export" in src


def test_dates_survive_serialisation():
    """``date`` is not a ``datetime``; a referral date quietly becoming null would be
    exactly the kind of loss this bundle exists to prevent."""
    from datetime import date, datetime, timezone
    from decimal import Decimal

    from services.analytics_service.app.routes.exit_bundle import _iso
    assert _iso(date(2026, 3, 1)) == "2026-03-01"
    assert _iso(datetime(2026, 3, 1, tzinfo=timezone.utc)).startswith("2026-03-01T")
    assert _iso(Decimal("1.5")) == "1.5"


def test_the_tenant_bundle_reports_a_failed_fetch_instead_of_swallowing_it(monkeypatch):
    """The regression that motivated this: three ``except: pass`` blocks that turned a
    missing branding record, a missing config set and a missing fraud record into a
    bundle that read as complete."""
    import inspect

    from services.tenant_service.app.services import TenantService
    src = inspect.getsource(TenantService.export_bundle)
    assert "problems.append" in src
    assert '"complete": not problems' in src
    assert "fraud_record" in src
