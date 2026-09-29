import { apiFetch } from "./client"

export interface BrandingOut {
  tenant_id: string
  display_name: string
  logo_url: string
  primary_color: string
  accent_color: string
  neutral_color: string
  default_theme: "light" | "dark"
  custom_domain: string
  updated_at: string
}

export interface BrandingUpsert {
  display_name?: string
  logo_url?: string
  primary_color?: string
  accent_color?: string
  neutral_color?: string
  default_theme?: "light" | "dark"
  custom_domain?: string
}

export function getBranding(tenantId: string, token: string) {
  return apiFetch<BrandingOut>(`/branding/${tenantId}`, { token })
}

export function putBranding(tenantId: string, payload: BrandingUpsert, token: string) {
  return apiFetch<BrandingOut>(`/branding/${tenantId}`, { method: "PUT", body: payload, token })
}
