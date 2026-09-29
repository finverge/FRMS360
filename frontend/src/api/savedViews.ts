import { apiFetch } from "./client"

// Mirrors services/analytics_service/app/routes/views.py. `query` is the dashboard's
// own filter query string (the same shape api/dashboards.ts's buildQuery() produces,
// minus the leading "?") - a saved view is "this dashboard, with these filters",
// nothing more structured than that on either side.
export interface SavedViewOut {
  id: string
  name: string
  dashboard: string
  query: string
  shared: boolean
  shared_roles: string[]
  audience: string
  owner: string
  mine: boolean
  updated_at: string
}

export interface SavedViewIn {
  name: string
  dashboard: string
  query: string
  shared: boolean
  shared_roles: string[]
}

export function listViews(tenantId: string, token: string) {
  return apiFetch<SavedViewOut[]>(`/analytics/${tenantId}/views`, { token })
}

export function saveView(tenantId: string, payload: SavedViewIn, token: string) {
  return apiFetch<SavedViewOut>(`/analytics/${tenantId}/views`, { method: "POST", body: payload, token })
}

export function deleteView(tenantId: string, viewId: string, token: string) {
  return apiFetch<void>(`/analytics/${tenantId}/views/${viewId}`, { method: "DELETE", token })
}

export interface ViewAudience {
  name: string
  label: string
}

export function listViewAudiences(tenantId: string, token: string) {
  return apiFetch<ViewAudience[]>(`/analytics/${tenantId}/view-audiences`, { token })
}
