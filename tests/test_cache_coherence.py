"""Configuration must look identical on every replica.

The bug being fixed: per-process TTL caches let replica A serve a threshold change while
replicas B, C and D still serve the previous one, so the numbers a user saw depended on
which replica the load balancer picked. These tests simulate replicas as separate
VersionedCache instances over the same backend - which is exactly what separate processes
are, minus the process boundary.
"""
import pytest

from cp_common.cache import (
    DatabaseBackend, MemoryBackend, UnsafeCacheConfiguration, VersionedCache,
    build_backend, verify_backend_supports,
)


def _replicas(backend, n=3, ttl=0.0):
    """n independent caches over one backend - one per notional replica."""
    return [VersionedCache(backend, "rules", ttl) for _ in range(n)]


# --------------------------------------------------------------- the actual bug
def test_a_config_change_reaches_every_replica_at_once():
    backend = DatabaseBackend()
    a, b, c = _replicas(backend)
    for i, r in enumerate((a, b, c)):
        r.put("tenant-1", {"threshold": 200}, r.current_generation())
    assert all(r.get("tenant-1")[0] == {"threshold": 200} for r in (a, b, c))

    # One replica handles the config change...
    a.invalidate()

    # ...and every replica, including those that never saw the request, drops its copy.
    for name, r in (("a", a), ("b", b), ("c", c)):
        value, _ = r.get("tenant-1")
        assert value is None, f"replica {name} kept a stale configuration"


def test_replicas_never_disagree_about_the_generation():
    backend = DatabaseBackend()
    a, b = _replicas(backend, 2)
    assert a.current_generation() == b.current_generation()
    a.invalidate()
    assert a.current_generation() == b.current_generation(), \
        "two replicas are on different generations of the same config"


def test_the_memory_backend_is_demonstrably_unsafe_for_replicas():
    """Pinning down *why* the default had to change.

    This is the old behaviour: two processes, two private caches, no agreement. The test
    asserts the flaw so that nobody 'simplifies' the default back to memory.
    """
    backend_a, backend_b = MemoryBackend(), MemoryBackend()
    a = VersionedCache(backend_a, "rules", 0.0)
    b = VersionedCache(backend_b, "rules", 0.0)
    a.put("t", {"threshold": 200}, a.current_generation())
    b.put("t", {"threshold": 200}, b.current_generation())

    a.invalidate()  # a config change handled by replica A only

    assert a.get("t")[0] is None
    assert b.get("t")[0] == {"threshold": 200}, \
        "MemoryBackend now shares state; update the docs and the startup guard"
    assert MemoryBackend.shared_across_replicas is False


# --------------------------------------------------------------- the startup guard
def test_multi_replica_on_a_process_local_cache_refuses_to_start():
    """Loud failure at boot beats a cluster that quietly reports different figures."""
    with pytest.raises(UnsafeCacheConfiguration) as err:
        verify_backend_supports(MemoryBackend(), replicas=4)
    assert "CACHE_BACKEND=database" in str(err.value), "the error does not say how to fix it"


def test_a_single_replica_may_use_the_memory_cache():
    verify_backend_supports(MemoryBackend(), replicas=1)  # must not raise


def test_the_database_backend_is_accepted_for_any_replica_count():
    verify_backend_supports(DatabaseBackend(), replicas=12)


def test_an_unknown_backend_name_fails_fast():
    with pytest.raises(ValueError) as err:
        build_backend("redis-ish")
    assert "Available:" in str(err.value)


# ------------------------------------------------------------------- integration
def test_config_activation_invalidates_the_analytics_cache(analytics_client, config_client,
                                                           token_for, tid):
    """End to end: a control-plane change must be visible to analytics immediately."""
    import uuid

    from services.analytics_service.app import rules

    rules.active_rules(tid)  # populate

    # A fresh draft, not a pre-seeded one - BR-715 makes activation maker-checker, so
    # reusing an already-active seeded config would 409 rather than actually change
    # anything. Creation itself bumps the generation (a new draft must be visible to
    # BR-311 simulation), so "before" is captured after it, not before.
    name = f"cache-coherence-{uuid.uuid4().hex[:10]}"
    made = config_client.post(f"/configs/{tid}", headers=token_for("tenant_admin"),
                              json={"kind": "rule", "name": name, "version": "1.0.0",
                                    "body": {"x": 1}})
    assert made.status_code == 201, made.text
    config_id = made.json()["id"]
    before = rules.cache_status()["rules_generation"]

    proposed = config_client.post(f"/configs/{tid}/{config_id}/activate",
                                  headers=token_for("tenant_admin"))
    assert proposed.status_code == 200, proposed.text
    mid = rules.cache_status()["rules_generation"]
    assert mid == before, "a mere proposal must not invalidate the cache"

    confirmed = config_client.post(f"/configs/{tid}/{config_id}/activate",
                                   headers=token_for("risk_manager"))
    assert confirmed.status_code == 200, confirmed.text

    after = rules.cache_status()["rules_generation"]
    assert after > before, "activating a config did not invalidate the analytics cache"

    # tid is session-scoped; leaving this fake "rule" active would join the tenant's
    # real rule catalogue for every test that runs after this one.
    from cp_common.db import SessionLocal
    from sqlalchemy import text as sql
    db = SessionLocal()
    try:
        db.execute(sql("DELETE FROM config.tenant_configs WHERE id = :i"), {"i": config_id})
        db.commit()
    finally:
        db.close()
    rules.invalidate()


def test_health_reports_the_cache_topology(analytics_client):
    """A misconfigured deployment should be visible, not inferred from odd numbers."""
    body = analytics_client.get("/health").json()
    assert "cache" in body, "health does not report cache topology"
    assert body["cache"]["backend"]
    assert "shared_across_replicas" in body["cache"]


def test_the_generation_read_is_authoritative_by_default():
    """A non-zero generation TTL re-opens the bug with a shorter window.

    This was found during implementation: with a 5-second backstop the fix looked
    complete and the end-to-end test still failed, because two replicas could be up to
    five seconds apart on the same tenant's configuration. Zero is the only value that
    makes the guarantee unconditional.
    """
    from cp_common import settings
    assert settings.cache_generation_ttl_seconds == 0.0, \
        "a non-zero generation TTL means replicas can disagree for that long"

    backend = DatabaseBackend()
    a, b = VersionedCache(backend, "rules"), VersionedCache(backend, "rules")
    a.put("t", {"v": 1}, a.current_generation())
    b.put("t", {"v": 1}, b.current_generation())
    b.invalidate()
    assert a.get("t")[0] is None, "replica A served a configuration its peer had dropped"
