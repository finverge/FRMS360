"""Query-engine selection.

The rest of the service talks only to the ``QueryEngine`` interface, so swapping the
analytical store is a one-line config change plus one new implementation module.
"""
import os

from .base import Filters, MetricResult, QueryEngine
from .postgres import PostgresEngine

_ENGINES = {"postgres": PostgresEngine}


def get_engine(session) -> QueryEngine:
    name = os.environ.get("ANALYTICS_ENGINE", "postgres").lower()
    try:
        return _ENGINES[name](session)
    except KeyError:
        raise RuntimeError(
            f"Unknown ANALYTICS_ENGINE '{name}'. Available: {', '.join(_ENGINES)}. "
            "A ClickHouse implementation slots in here by adding ClickHouseEngine(QueryEngine)."
        )


__all__ = ["QueryEngine", "Filters", "MetricResult", "PostgresEngine", "get_engine"]
