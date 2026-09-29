"""Cross-replica configuration cache.

The problem this exists to solve is a correctness bug, not a performance one.

``analytics_service`` used to cache each tenant's rule catalogue and FRM policy in a
module-level dict with a 60-second TTL. That is fine with one process. Run four replicas
behind a load balancer and the same dashboard, refreshed twice, can return two different
sets of numbers - because replica 1 picked up a threshold change and replicas 2 to 4 are
still serving the previous one for up to a minute. Which numbers you get depends on which
replica the load balancer happened to pick. For a platform whose central claim is that its
figures reconcile, that is not acceptable staleness; it is the platform being wrong.

The fix is to stop caching against wall-clock time and start caching against a **shared
generation token**. Every replica reads the same token; a replica whose cached copy was
built under an older token discards it. Config changes therefore take effect everywhere at
once, and two replicas can never be serving different generations of the same tenant's
configuration.

Backends
--------
``memory``    Single-process only. Correct when there is exactly one replica, and refused
              outright when there is more than one - see ``verify_backend_supports``.
``database``  The default. Uses the Postgres instance the platform already runs, so
              multi-replica correctness needs no new infrastructure.
``redis``     Not implemented yet. The interface below is what it would implement; it is
              the right answer once generation reads become hot enough to be worth moving
              off the database.
"""
from __future__ import annotations

import abc
import threading
import time


class CacheBackend(abc.ABC):
    """A generation counter plus a small key/value store, shared across replicas."""

    name = "abstract"
    #: Whether two processes using this backend observe the same state.
    shared_across_replicas = False

    @abc.abstractmethod
    def generation(self, namespace: str) -> int:
        """The current version of ``namespace``. Every replica must see the same value."""

    @abc.abstractmethod
    def bump(self, namespace: str) -> int:
        """Invalidate ``namespace`` everywhere. Returns the new generation."""


class MemoryBackend(CacheBackend):
    """Process-local. Only ever correct with a single replica."""

    name = "memory"
    shared_across_replicas = False

    def __init__(self) -> None:
        self._gen: dict[str, int] = {}
        self._lock = threading.Lock()

    def generation(self, namespace: str) -> int:
        with self._lock:
            return self._gen.setdefault(namespace, 1)

    def bump(self, namespace: str) -> int:
        with self._lock:
            self._gen[namespace] = self._gen.get(namespace, 1) + 1
            return self._gen[namespace]


class DatabaseBackend(CacheBackend):
    """Generations in the shared Postgres instance.

    A single-row upsert and a primary-key read - negligible next to the analytical
    queries these values guard, and it makes multi-replica deployment correct today
    without introducing another piece of infrastructure to run and monitor.
    """

    name = "database"
    shared_across_replicas = True

    def generation(self, namespace: str) -> int:
        from sqlalchemy import text

        from .db import SessionLocal
        db = SessionLocal()
        try:
            row = db.execute(
                text("SELECT generation FROM config_generation WHERE namespace = :ns"),
                {"ns": namespace}).first()
            return int(row[0]) if row else 1
        finally:
            db.close()

    def bump(self, namespace: str) -> int:
        from sqlalchemy import text

        from .db import SessionLocal
        db = SessionLocal()
        try:
            # Atomic even when several services bump concurrently.
            row = db.execute(text(
                "INSERT INTO config_generation (namespace, generation, updated_at) "
                "VALUES (:ns, 2, NOW()) "
                "ON CONFLICT (namespace) DO UPDATE "
                "SET generation = config_generation.generation + 1, updated_at = NOW() "
                "RETURNING generation"), {"ns": namespace}).first()
            db.commit()
            return int(row[0])
        finally:
            db.close()


_BACKENDS = {"memory": MemoryBackend, "database": DatabaseBackend}


def build_backend(name: str) -> CacheBackend:
    if name not in _BACKENDS:
        raise ValueError(
            "Unknown cache backend '" + name + "'. Available: " +
            ", ".join(sorted(_BACKENDS)))
    return _BACKENDS[name]()


class UnsafeCacheConfiguration(RuntimeError):
    """Raised at startup rather than serving quietly divergent numbers."""


def verify_backend_supports(backend: CacheBackend, replicas: int) -> None:
    """Refuse to start a multi-replica deployment on a process-local cache.

    Failing loudly at boot is the whole point. The alternative - starting anyway - gives
    a cluster that looks healthy, passes every health check, and intermittently reports
    different figures to different users with nothing in the logs to explain it. That is
    precisely the class of silent degradation this codebase keeps having to design out.
    """
    if replicas > 1 and not backend.shared_across_replicas:
        raise UnsafeCacheConfiguration(
            "CACHE_BACKEND='" + backend.name + "' is process-local, but APP_REPLICAS=" +
            str(replicas) + ". Replicas would cache different generations of tenant "
            "configuration and report different figures for the same query. "
            "Set CACHE_BACKEND=database (or run a single replica).")


class VersionedCache:
    """A small read-through cache whose entries expire by generation, not by clock.

    ``ttl_seconds`` defaults to 0, meaning the generation is read afresh every time and
    replicas can never disagree. A non-zero value is a deliberate trade: it saves a
    primary-key read at the cost of a divergence window exactly that long, which is a
    smaller version of the original bug rather than a different thing. Callers that set it
    should know what they are buying.
    """

    def __init__(self, backend: CacheBackend, namespace: str, ttl_seconds: float = 0.0):
        self.backend = backend
        self.namespace = namespace
        self.ttl_seconds = ttl_seconds
        self._entries: dict[str, tuple[int, object]] = {}
        self._gen_checked_at = 0.0
        self._gen = 0
        self._lock = threading.Lock()

    def current_generation(self) -> int:
        now = time.monotonic()
        with self._lock:
            fresh = self._gen and (now - self._gen_checked_at) < self.ttl_seconds
            if fresh:
                return self._gen
        gen = self.backend.generation(self.namespace)
        with self._lock:
            self._gen, self._gen_checked_at = gen, now
        return gen

    def get(self, key: str):
        """Returns (value, generation), or (None, generation) on a miss."""
        gen = self.current_generation()
        with self._lock:
            hit = self._entries.get(key)
        if hit and hit[0] == gen:
            return hit[1], gen
        return None, gen

    def put(self, key: str, value, generation: int) -> None:
        with self._lock:
            self._entries[key] = (generation, value)

    def invalidate(self) -> int:
        """Bump the shared generation - every replica drops this namespace."""
        gen = self.backend.bump(self.namespace)
        with self._lock:
            self._gen, self._gen_checked_at = gen, time.monotonic()
        return gen
