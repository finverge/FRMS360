import { apiFetch } from "./client"

// Mirrors services/analytics_service/app/schemas.py's FilterQuery/DashboardOut. Only
// the fields the migrated dashboards so far (Analyst, EWS, RFA) actually use are
// modelled here - the full ~20-field filter set (rails, products, segments, typologies,
// assignees, fmr/str status, nj_breach_only, ...) still needs wiring for the rest.
export interface DashboardFilters {
  date_from?: string
  date_to?: string
  severities?: string[]
  // Pins Account 360 to one account; without it, that dashboard shows the population
  // so an investigator can pick one from the rows (see routes/dashboards.py's
  // account360_dashboard docstring). Unused by every other persona so far.
  account?: string
}

export interface MetricOut {
  name: string
  label: string
  value: number
  unit: "inr_paise" | "ratio" | "number" | "seconds" | string
  volatile: boolean
}

// A configured (quantitative) rule that produced no alert in the current window - see
// rules.py's dormant_report().
export interface DormantRule {
  rule_id: string
  family: string
  reason: string
  threshold: number | null
  unit: string
  blocked_by: string | null
}

export interface DormantReport {
  available: boolean
  configured: number
  configured_quantitative?: number
  fired: number
  fired_quantitative?: number
  dormant: DormantRule[]
  blocked_count?: number
  dormant_qualitative: string[]
  coverage: number | null
  uncatalogued: string[]
}

// One data-integrity invariant (e.g. "every alert resolves to a transaction") checked
// live against the current filter set - see reconciliation.py's run_all()/_mk().
export interface ReconciliationCheck {
  id: string
  title: string
  severity: "critical" | "high" | "medium" | string
  passed: boolean
  expected: number
  actual: number
  delta: number
  detail: string
}

export interface ReconciliationOut {
  status: "pass" | "fail"
  checked_at: string | null
  summary: { total: number; passed: number; failed: number; critical_failures: number }
  checks: ReconciliationCheck[]
}

export interface DashboardOut {
  dashboard: string
  persona: string
  generated_at: string
  filters: Record<string, unknown>
  metrics: Record<string, MetricOut>
  breakdowns: Record<string, { key: string; value: number }[]>
  rows: Record<string, unknown>[]
  dormant?: DormantReport | null
  reconciliation?: ReconciliationOut | null
}

function buildQuery(filters: DashboardFilters): string {
  const p = new URLSearchParams()
  if (filters.date_from) p.set("date_from", filters.date_from)
  if (filters.date_to) p.set("date_to", filters.date_to)
  for (const s of filters.severities ?? []) p.append("severities", s)
  if (filters.account) p.set("account", filters.account)
  const qs = p.toString()
  return qs ? `?${qs}` : ""
}

// One fetcher for every persona - each dashboard key maps to a
// GET /analytics/{tenant_id}/{dashboard} route with an identical filter query shape
// (services/analytics_service/app/routes/dashboards.py); only the metrics/breakdowns/
// rows/dormant fields returned differ per persona.
export function getDashboard(tenantId: string, dashboard: string, filters: DashboardFilters, token: string) {
  return apiFetch<DashboardOut>(`/analytics/${tenantId}/${dashboard}${buildQuery(filters)}`, { token })
}
