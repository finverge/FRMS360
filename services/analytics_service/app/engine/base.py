"""Engine interface + the filter model shared by every dashboard.

Filters are declared once here and applied uniformly by the engine. That uniformity is
half of the reconciliation guarantee: two dashboards asking for the same metric with the
same filters cannot diverge, because they traverse the identical code path.
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass
class Filters:
    """The global filter set. ``None`` means 'no constraint on this dimension'."""
    tenant_id: str = ""
    date_from: datetime | None = None
    date_to: datetime | None = None
    rails: list[str] = field(default_factory=list)
    families: list[str] = field(default_factory=list)
    severities: list[str] = field(default_factory=list)
    regions: list[str] = field(default_factory=list)
    products: list[str] = field(default_factory=list)
    segments: list[str] = field(default_factory=list)
    states: list[str] = field(default_factory=list)
    fmr_categories: list[str] = field(default_factory=list)
    dispositions: list[str] = field(default_factory=list)
    min_amount_paise: int | None = None
    max_amount_paise: int | None = None
    # Account 360 pins every figure to one account (either side of the txn).
    account: str | None = None
    # Free-text substring match against the entity's own identifier and account
    # columns (case_id / alert_id+rule_id / txn_id+debtor+creditor). Unlike `account`
    # above this is not an exact pin - it is what backs the row tables' search boxes.
    id_search: str | None = None
    # --- entity ---
    branches: list[str] = field(default_factory=list)
    # --- signal ---
    rules: list[str] = field(default_factory=list)
    typologies: list[str] = field(default_factory=list)
    analysts: list[str] = field(default_factory=list)
    config_versions: list[str] = field(default_factory=list)
    # --- case ---
    assignees: list[str] = field(default_factory=list)
    rfa_only: bool = False
    # --- regulatory ---
    # filed | due | overdue  (FMR track). Independent of the STR track below.
    fmr_status: str | None = None
    str_status: str | None = None
    nj_breach_only: bool = False
    # --- provenance (BR-611) ---
    # Empty means "no constraint", i.e. live and demonstration rows together. That is the
    # right default for a demo instance and the wrong one to quote a figure from, which is
    # why every response also reports the mix rather than relying on this being set.
    sources: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        """Stable representation - echoed back on every response so a figure is
        always reproducible from the filter set that produced it."""
        return {
            "tenant_id": self.tenant_id,
            "date_from": self.date_from.isoformat() if self.date_from else None,
            "date_to": self.date_to.isoformat() if self.date_to else None,
            "rails": self.rails, "families": self.families, "severities": self.severities,
            "regions": self.regions, "products": self.products, "segments": self.segments,
            "states": self.states, "fmr_categories": self.fmr_categories,
            "dispositions": self.dispositions,
            "min_amount_paise": self.min_amount_paise,
            "max_amount_paise": self.max_amount_paise,
            "account": self.account, "id_search": self.id_search,
            "branches": self.branches, "rules": self.rules,
            "typologies": self.typologies, "analysts": self.analysts,
            "config_versions": self.config_versions,
            "assignees": self.assignees, "rfa_only": self.rfa_only,
            "fmr_status": self.fmr_status, "str_status": self.str_status,
            "nj_breach_only": self.nj_breach_only,
            "sources": self.sources,
        }


@dataclass
class MetricResult:
    name: str
    label: str
    value: float | int
    unit: str
    volatile: bool = False


class QueryEngine(ABC):
    """Every analytical read goes through this interface."""

    @abstractmethod
    def metric(self, name: str, filters: Filters) -> MetricResult:
        """Evaluate one named metric from the registry."""

    @abstractmethod
    def breakdown(self, name: str, dimension: str, filters: Filters) -> list[dict[str, Any]]:
        """Evaluate a metric grouped by one dimension. Must partition exactly:
        the sum of the groups equals the ungrouped metric (invariant R-06/R-07)."""

    @abstractmethod
    def rows(self, entity: str, filters: Filters, limit: int, offset: int) -> list[dict[str, Any]]:
        """Drill-down rows: 'alert' | 'case' | 'transaction'."""

    @abstractmethod
    def score_sample(self, filters: Filters, limit: int = 20000) -> list[float]:
        """Raw alert scores for the filtered window."""

    @abstractmethod
    def count(self, entity: str, filters: Filters) -> int:
        """Total matching rows, ignoring paging."""

    @abstractmethod
    def timeseries(self, name: str, bucket: str, filters: Filters) -> list[dict[str, Any]]:
        """One metric grouped into day/week/month buckets."""

    @abstractmethod
    def network(self, tenant_id: str, filters: Filters, case_id: str | None = None,
                account: str | None = None, limit: int = 300) -> dict[str, Any]:
        """Account-to-account graph for link analysis."""

    @abstractmethod
    def evidence(self, tenant_id: str, entity: str, ident: str) -> dict[str, Any]:
        """Terminal drill stage: one record plus its supporting trail."""

    @abstractmethod
    def mule_risk_indicators(self, tenant_id: str, account: str) -> dict[str, Any]:
        """Behavioural/network/velocity signals for one account, over 1D/1W/1M."""

    @abstractmethod
    def scalar_sql(self, sql: str, params: dict) -> Any:
        """Escape hatch for the reconciliation checks, which assert on raw aggregates."""

    @abstractmethod
    def all_sql(self, sql: str, params: dict) -> list[tuple]:
        """Escape hatch returning rows, for governance reporting."""
