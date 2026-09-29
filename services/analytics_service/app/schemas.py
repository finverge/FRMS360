"""Request/response contracts for the analytics service."""
from datetime import datetime

from pydantic import BaseModel, Field

from .engine.base import Filters


class FilterQuery(BaseModel):
    """Query-string filter model, converted to the engine's Filters."""
    date_from: datetime | None = None
    date_to: datetime | None = None
    rails: list[str] = Field(default_factory=list)
    families: list[str] = Field(default_factory=list)
    severities: list[str] = Field(default_factory=list)
    regions: list[str] = Field(default_factory=list)
    products: list[str] = Field(default_factory=list)
    segments: list[str] = Field(default_factory=list)
    states: list[str] = Field(default_factory=list)
    fmr_categories: list[str] = Field(default_factory=list)
    dispositions: list[str] = Field(default_factory=list)
    min_amount_paise: int | None = None
    max_amount_paise: int | None = None
    account: str | None = None
    id_search: str | None = None
    branches: list[str] = Field(default_factory=list)
    rules: list[str] = Field(default_factory=list)
    typologies: list[str] = Field(default_factory=list)
    analysts: list[str] = Field(default_factory=list)
    config_versions: list[str] = Field(default_factory=list)
    assignees: list[str] = Field(default_factory=list)
    rfa_only: bool = False
    fmr_status: str | None = None
    str_status: str | None = None
    nj_breach_only: bool = False
    #: Provenance filter (BR-611). Empty = live and demonstration data together.
    sources: list[str] = Field(default_factory=list)

    def to_filters(self, tenant_id: str) -> Filters:
        return Filters(tenant_id=tenant_id, **self.model_dump())


class MetricOut(BaseModel):
    name: str
    label: str
    # int first: money is carried in integer paise and must not be coerced to float,
    # which would round large values and break exact reconciliation.
    value: int | float
    unit: str
    # Point-in-time metric (depends on NOW()); not reproducible as-of a date.
    volatile: bool = False


class DashboardOut(BaseModel):
    dashboard: str
    persona: str
    generated_at: datetime
    filters: dict
    metrics: dict[str, MetricOut]
    breakdowns: dict[str, list[dict]] = Field(default_factory=dict)
    rows: list[dict] = Field(default_factory=list)
    reconciliation: dict | None = None
    dormant: dict | None = None


class NetworkLinkageOut(BaseModel):
    linked_devices: int
    linked_accounts: int
    linked_ips: int
    # Fraud360 captures no lat/long or geocoding - these stand in for Verafye's
    # "Locations" honestly, as what the ledger can actually attest to.
    distinct_branches: int
    distinct_regions: int


class TransactionFlowOut(BaseModel):
    funds_in_counterparties: int
    funds_out_counterparties: int
    funds_in_txn_count: dict[str, int]
    funds_out_txn_count: dict[str, int]
    max_txn_paise: int


class VelocityOut(BaseModel):
    funds_in_paise: int
    funds_out_paise: int
    net_flow_paise: int
    avg_txn_paise: int | float


class MuleRiskIndicatorsOut(BaseModel):
    account: str
    total_txn_count: int
    network_linkage: NetworkLinkageOut
    transaction_flow: TransactionFlowOut
    velocity: VelocityOut


class OfacMatchOut(BaseModel):
    ent_num: int
    name: str
    type: str
    program: str
    remarks: str
    matched_on: str
    matched_via: str
    score: float


class OfacScreenOut(BaseModel):
    query: str
    matches: list[OfacMatchOut]
    list_size: int
    list_refreshed_at: str | None = None


class GeolocateOut(BaseModel):
    ip: str
    locatable: bool
    reason: str | None = None
    lat: float | None = None
    lon: float | None = None
    city: str | None = None
    region: str | None = None
    country: str | None = None
    isp: str | None = None


class AiInsightsOut(BaseModel):
    entity: str
    id: str
    narrative: str
    decision: str
    risk_score: int
    confidence: int
    recommendations: list[str]
    model: str
    generated_at: str
