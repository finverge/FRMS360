import { apiFetch } from "./client"

// Mirrors services/analytics_service/app/routes/dashboards.py's evidence()/graph()/
// drill() - the "stage 6" terminal drill and the account-graph behind a case or
// account. Both share the same three-condition PII reveal gate (_resolve_reveal): the
// role must hold can_reveal_pii, the caller must ask explicitly, and a justification of
// at least 8 characters is required - anything less returns masked data, never an error
// that stops the view from loading.
export type EvidenceEntity = "alert" | "case" | "transaction"

export interface RevealOptions {
  reveal?: boolean
  justification?: string
}

function revealParams(opts: RevealOptions): string {
  if (!opts.reveal) return ""
  const p = new URLSearchParams({ reveal: "true" })
  if (opts.justification) p.set("justification", opts.justification)
  return `?${p}`
}

// The payload shape genuinely differs per entity (see engine/postgres.py's evidence()):
// alert -> {alert, transaction, case?, sibling_alerts?}; case -> {case, alerts,
// transactions, value_check}; transaction -> {transaction, alerts}. Modelled loosely
// since every field within is itself a masked/unmasked raw row.
export interface EvidenceOut {
  entity: string
  id: string
  error?: string
  alert?: Record<string, unknown>
  transaction?: Record<string, unknown>
  case?: Record<string, unknown>
  sibling_alerts?: Record<string, unknown>[]
  alerts?: Record<string, unknown>[]
  transactions?: Record<string, unknown>[]
  value_check?: { stated_paise: number; derived_paise: number; reconciled: boolean }
}

export function getEvidence(
  tenantId: string,
  entity: EvidenceEntity,
  ident: string,
  opts: RevealOptions,
  token: string
) {
  return apiFetch<EvidenceOut>(`/analytics/${tenantId}/evidence/${entity}/${ident}${revealParams(opts)}`, { token })
}

export interface GraphNode {
  id: string
  degree: number
  role: "hub" | "collector" | "disburser" | string
  in_paise: number
  out_paise: number
}

export interface GraphEdge {
  source: string
  target: string
  count: number
  value_paise: number
}

export interface GraphOut {
  nodes: GraphNode[]
  edges: GraphEdge[]
  scope: { case_id: string | null; account: string | null }
  pii_masked: boolean
}

export function getGraph(
  tenantId: string,
  scope: { case_id?: string; account?: string },
  opts: RevealOptions,
  token: string
) {
  const p = new URLSearchParams()
  if (scope.case_id) p.set("case_id", scope.case_id)
  if (scope.account) p.set("account", scope.account)
  if (opts.reveal) {
    p.set("reveal", "true")
    if (opts.justification) p.set("justification", opts.justification)
  }
  const qs = p.toString()
  return apiFetch<GraphOut>(`/analytics/${tenantId}/graph${qs ? `?${qs}` : ""}`, { token })
}

// Mirrors services/analytics_service/app/schemas.py's MuleRiskIndicatorsOut. Pure
// aggregates (counts/sums) only - no raw device id, IP, or counterparty account number
// is ever in this payload, so unlike evidence()/graph() there is no reveal gate to thread
// through here.
export interface MuleRiskIndicatorsOut {
  account: string
  total_txn_count: number
  network_linkage: {
    linked_devices: number
    linked_accounts: number
    linked_ips: number
    distinct_branches: number
    distinct_regions: number
  }
  transaction_flow: {
    funds_in_counterparties: number
    funds_out_counterparties: number
    funds_in_txn_count: { "1d": number; "1w": number; "1m": number }
    funds_out_txn_count: { "1d": number; "1w": number; "1m": number }
    max_txn_paise: number
  }
  velocity: {
    funds_in_paise: number
    funds_out_paise: number
    net_flow_paise: number
    avg_txn_paise: number
  }
}

export function getMuleRiskIndicators(tenantId: string, account: string, token: string) {
  return apiFetch<MuleRiskIndicatorsOut>(
    `/analytics/${tenantId}/mule-risk/${encodeURIComponent(account)}`,
    { token }
  )
}

// Mirrors routes/dashboards.py's drill() - filter-preserving, paginated rows. Every row
// table fetches its own page through this (see useRowsPage.ts) rather than displaying
// whatever fixed-size window the dashboard endpoint happened to bundle - id_search, when
// given, applies server-side across the *entire* filtered dataset, not just this page.
export interface DrillOut {
  entity: string
  count: number
  total: number
  limit: number
  offset: number
  has_more: boolean
  pii_masked: boolean
  rows: Record<string, unknown>[]
}

// Mirrors routes/dashboards.py's ai_insights_route() - a real, locally self-hosted LLM
// (Ollama/vLLM, never a paid hosted API - see ai_insights.py's LLM_BASE_URL) reading the
// same masked/unmasked evidence payload the caller's own reveal grant entitles them to.
// POST because it's a real generation call each time, not a cached read - matching
// Verafye's own explicit "Generate AI Insights" button rather than an automatic panel.
export interface AiInsightsOut {
  entity: string
  id: string
  narrative: string
  decision: "ALLOW" | "HOLD" | "BLOCK" | string
  risk_score: number
  confidence: number
  recommendations: string[]
  model: string
  generated_at: string
}

export function generateAiInsights(
  tenantId: string,
  entity: EvidenceEntity,
  ident: string,
  opts: RevealOptions,
  token: string
) {
  return apiFetch<AiInsightsOut>(
    `/analytics/${tenantId}/ai-insights/${entity}/${ident}${revealParams(opts)}`,
    { method: "POST", token }
  )
}

// Mirrors routes/dashboards.py's geolocate_transaction() - a real IP-geolocation
// lookup (ip-api.com) against the transaction's own real ip_addr. There is no lat/long
// in the data model, so this always requires reveal - unlike ai-insights, there is no
// masked-data path.
export interface GeolocateOut {
  ip: string
  locatable: boolean
  reason?: string | null
  lat?: number | null
  lon?: number | null
  city?: string | null
  region?: string | null
  country?: string | null
  isp?: string | null
}

export function geolocateTransaction(
  tenantId: string,
  txnId: string,
  opts: RevealOptions,
  token: string
) {
  return apiFetch<GeolocateOut>(
    `/analytics/${tenantId}/geolocate/${txnId}${revealParams(opts)}`,
    { method: "POST", token }
  )
}

export function fetchRowsPage(
  tenantId: string,
  entity: EvidenceEntity,
  params: { baseQuery?: string; idSearch?: string; limit: number; offset: number },
  token: string
): Promise<DrillOut> {
  const p = new URLSearchParams(params.baseQuery ?? "")
  if (params.idSearch) p.set("id_search", params.idSearch)
  p.set("limit", String(params.limit))
  p.set("offset", String(params.offset))
  return apiFetch<DrillOut>(`/analytics/${tenantId}/drill/${entity}?${p.toString()}`, { token })
}
