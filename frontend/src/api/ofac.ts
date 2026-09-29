import { apiFetch } from "./client"

// Mirrors services/analytics_service/app/routes/dashboards.py's ofac_screen() - fuzzy
// match against the real OFAC SDN list (services/analytics_service/app/ofac.py),
// refreshed by scripts/load_ofac_sdn.py. Investigator-driven: Fraud360's transaction/
// alert/case rows carry account numbers, not customer names, so there is nothing to
// screen automatically - the analyst types in a name obtained elsewhere (a case note, a
// KYC document, a call transcript).
export interface OfacMatch {
  ent_num: number
  name: string
  type: string
  program: string
  remarks: string
  matched_on: string
  matched_via: "primary" | "alias" | string
  score: number
}

export interface OfacScreenOut {
  query: string
  matches: OfacMatch[]
  list_size: number
  list_refreshed_at: string | null
}

export function screenOfac(tenantId: string, name: string, token: string) {
  const p = new URLSearchParams({ name })
  return apiFetch<OfacScreenOut>(`/analytics/${tenantId}/ofac-screen?${p.toString()}`, { token })
}
