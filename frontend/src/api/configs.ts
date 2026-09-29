import { apiFetch } from "./client"

// Mirrors config_service/app/schemas.py. "model" and "decision_policy" are valid kinds
// server-side (BR-807/808, BR-316) but have no editor here yet - same as this console's
// other not-yet-migrated sections, they're left to :8080 until there's a real UI for
// their bodies (champion/challenger model cards, per-rail routing) to design around.
export type ConfigKind = "typology" | "rule" | "network_map" | "policy"

export interface ConfigOut {
  id: string
  tenant_id: string
  kind: string
  name: string
  version: string
  // draft --propose--> pending_activation --confirm--> active, or --reject--> draft.
  // "archived" is a config a newer version superseded. See maker_checker.py.
  status: "draft" | "pending_activation" | "active" | "archived" | string
  body: Record<string, unknown>
  created_at: string
  proposed_by: string | null
  proposed_by_role: string | null
  proposed_at: string | null
  approved_by: string | null
  approved_by_role: string | null
  approved_at: string | null
}

export interface ConfigCreate {
  kind: ConfigKind
  name: string
  version: string
  body: Record<string, unknown>
}

export function listConfigs(tenantId: string, token: string) {
  return apiFetch<ConfigOut[]>(`/configs/${tenantId}`, { token })
}

export function createConfig(tenantId: string, payload: ConfigCreate, token: string) {
  return apiFetch<ConfigOut>(`/configs/${tenantId}`, { method: "POST", body: payload, token })
}

// BR-715 maker-checker: the first eligible caller's POST proposes (draft -> pending_
// activation); a second, different eligible caller's POST against the same config
// confirms it (-> active). Same endpoint, same function - the backend decides which
// phase this call is from the config's current status.
export function activateConfig(tenantId: string, configId: string, token: string) {
  return apiFetch<ConfigOut>(`/configs/${tenantId}/${configId}/activate`, { method: "POST", token })
}

export function rejectActivation(tenantId: string, configId: string, token: string) {
  return apiFetch<ConfigOut>(`/configs/${tenantId}/${configId}/reject-activation`, { method: "POST", token })
}

export interface BackfillSimulateResult {
  transactions: number
  would_fire: Record<string, number>
  actual: Record<string, number>
  note: string
}

// BR-311: read-only. Nothing here writes a config - the operator still has to create
// and activate a version for a threshold to take effect.
export function simulateBackfill(
  tenantId: string,
  payload: { window_from: string; window_to: string; thresholds: Record<string, number> },
  token: string
) {
  return apiFetch<BackfillSimulateResult>(`/analytics/${tenantId}/detection/backfill`, {
    method: "POST",
    body: { ...payload, mode: "simulate" },
    token,
  })
}
