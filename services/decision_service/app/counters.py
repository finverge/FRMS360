"""Reading precomputed observations on the hot path.

One rule governs everything here: **the decision path performs lookups, never
aggregations.** The moment a decision needs `SUM(...) OVER (...)` it has left the budget,
and no amount of tuning brings it back. Aggregation happens on the write path, where a
few milliseconds do not matter.

The store is behind an interface for the reason the query engine is: PostgreSQL is
correct and needs no new infrastructure, and it is almost certainly not what a bank
running UPI at peak will deploy. Swapping in Redis, Valkey or Aerospike must be a
configuration change, not a rewrite — and the shadow-mode measurement is precisely what
tells us whether that swap is needed.

**A stale counter is not a counter.** Past its freshness horizon a value is reported
absent rather than used, so a rule reading it is recorded as skipped. Scoring a velocity
window from yesterday's count would be worse than not scoring it: it would look like the
control ran.
"""
from __future__ import annotations

import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from sqlalchemy import text
from sqlalchemy.orm import Session

#: A counter older than this is treated as absent. Deliberately tight: these are rolling
#: windows maintained on every write, so anything this old means the writer has stopped.
DEFAULT_FRESHNESS_SECONDS = 900


@dataclass
class Lookup:
    """What a single account lookup returned, and what it cost."""
    values: dict[str, float] = field(default_factory=dict)
    stale: list[str] = field(default_factory=list)
    took_ms: float = 0.0
    available: bool = True
    error: str = ""


class CounterStore(ABC):
    """Every read on the decision path goes through this."""

    name = "abstract"

    @abstractmethod
    def read(self, tenant_id: str, account: str, observations: list[str]) -> Lookup:
        """All observations for one account, in one round trip.

        One round trip is the contract, not an optimisation: N sequential lookups is how
        a 5 ms budget becomes 50 ms.
        """

    @abstractmethod
    def write(self, tenant_id: str, account: str, values: dict[str, float]) -> None:
        """Maintain counters. Called from the write path, never from a decision."""


class PostgresCounterStore(CounterStore):
    """Reference implementation. Correct, needs no new infrastructure, not the fastest.

    Adequate for shadow mode and for tenants whose peak is modest. A single indexed
    lookup keyed on (tenant, account) returning a handful of rows is a few milliseconds
    against a warm cache — the measurement will say whether that is good enough.
    """

    name = "postgres"

    def __init__(self, db: Session, freshness_seconds: int = DEFAULT_FRESHNESS_SECONDS):
        self.db = db
        self.freshness = freshness_seconds

    def read(self, tenant_id: str, account: str, observations: list[str]) -> Lookup:
        started = time.perf_counter()
        out = Lookup()
        if not observations:
            out.took_ms = (time.perf_counter() - started) * 1000
            return out
        try:
            rows = self.db.execute(text(
                "SELECT observation, value, as_of FROM decision.account_counters "
                "WHERE tenant_id = :t AND account = :a "
                "  AND observation = ANY(:obs)"),
                {"t": tenant_id, "a": account, "obs": list(observations)}
            ).mappings().all()
        except Exception as exc:  # noqa: BLE001
            out.available = False
            out.error = str(exc)[:200]
            out.took_ms = (time.perf_counter() - started) * 1000
            return out

        horizon = datetime.now(timezone.utc) - timedelta(seconds=self.freshness)
        for r in rows:
            as_of = r["as_of"]
            if as_of and as_of.tzinfo is None:
                as_of = as_of.replace(tzinfo=timezone.utc)
            if as_of and as_of < horizon:
                out.stale.append(r["observation"])
                continue
            out.values[r["observation"]] = float(r["value"])
        out.took_ms = (time.perf_counter() - started) * 1000
        return out

    def write(self, tenant_id: str, account: str, values: dict[str, float]) -> None:
        if not values:
            return
        for observation, value in values.items():
            self.db.execute(text(
                "INSERT INTO decision.account_counters "
                "  (tenant_id, account, observation, value, as_of, updated_at) "
                "VALUES (:t, :a, :o, :v, now(), now()) "
                "ON CONFLICT (tenant_id, account, observation) DO UPDATE "
                "  SET value = EXCLUDED.value, as_of = now(), updated_at = now()"),
                {"t": tenant_id, "a": account, "o": observation, "v": float(value)})


class MemoryCounterStore(CounterStore):
    """In-process store, for tests and for measuring the path without the database.

    Its real job is to isolate the framework and evaluation cost from the store cost.
    If the total budget is missed with this store, the runtime is the problem; if it is
    met here and missed with Postgres, the store is.
    """

    name = "memory"

    def __init__(self):
        self._data: dict[tuple[str, str], dict[str, float]] = {}

    def read(self, tenant_id: str, account: str, observations: list[str]) -> Lookup:
        started = time.perf_counter()
        held = self._data.get((tenant_id, account), {})
        out = Lookup(values={k: v for k, v in held.items() if k in set(observations)})
        out.took_ms = (time.perf_counter() - started) * 1000
        return out

    def write(self, tenant_id: str, account: str, values: dict[str, float]) -> None:
        self._data.setdefault((tenant_id, account), {}).update(
            {k: float(v) for k, v in values.items()})


def build(kind: str, db: Session | None = None) -> CounterStore:
    """Select the store. Unknown kinds are refused rather than defaulted.

    Falling back to a working store on a typo would mean a deployment silently running
    the slow path in production and nobody finding out until the latency SLO was missed.
    """
    kind = (kind or "postgres").strip().lower()
    if kind == "postgres":
        if db is None:
            raise ValueError("the postgres counter store needs a session")
        return PostgresCounterStore(db)
    if kind == "memory":
        return MemoryCounterStore()
    raise ValueError(
        f"unknown counter store '{kind}'. Supported: postgres, memory. "
        "A store is not defaulted - running the wrong one is invisible until it is slow.")
