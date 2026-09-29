import { apiFetch } from "./client"

// Mirrors services/tenant_service/app/routes/sso.py's admin CRUD
// (/auth/sso/admin/{tenant_id}/providers) and schemas.py's IdpCreate/IdpUpdate.
export interface IdpOut {
  id: string
  slug: string
  display_name: string
  protocol: "oidc" | "saml"
  issuer: string
  client_id: string
  discovery_url: string
  authorize_url: string
  token_url: string
  jwks_url: string
  scopes: string
  saml_sso_url: string
  saml_email_attribute: string
  saml_groups_attribute: string
  email_claim: string
  groups_claim: string
  role_mapping: Record<string, string>
  default_role: string
  jit_provisioning: boolean
  enabled: boolean
  // The client secret and SAML certificate are never returned, not even to the
  // tenant's own administrator - only whether one is on file (see sso.py's
  // _provider_out), so the console can show "unchanged" instead of a blank that
  // looks like nothing was ever set.
  has_client_secret: boolean
  has_saml_certificate: boolean
  created_at: string
}

export interface IdpWrite {
  slug?: string
  display_name?: string
  protocol?: "oidc" | "saml"
  issuer?: string
  client_id?: string
  // Sending "" on update means "leave as it is" - the field is never pre-filled from
  // the server, so the form cannot distinguish "untouched" from "cleared" any other
  // way (see schemas.py's IdpUpdate docstring). Omit entirely to leave unset on create.
  client_secret?: string
  discovery_url?: string
  authorize_url?: string
  token_url?: string
  jwks_url?: string
  scopes?: string
  saml_sso_url?: string
  saml_certificate?: string
  saml_email_attribute?: string
  saml_groups_attribute?: string
  email_claim?: string
  groups_claim?: string
  role_mapping?: Record<string, string>
  default_role?: string
  jit_provisioning?: boolean
  enabled?: boolean
}

export function listProviders(tenantId: string, token: string) {
  return apiFetch<IdpOut[]>(`/auth/sso/admin/${tenantId}/providers`, { token })
}

export function createProvider(tenantId: string, payload: IdpWrite & { slug: string; display_name: string }, token: string) {
  return apiFetch<IdpOut>(`/auth/sso/admin/${tenantId}/providers`, { method: "POST", body: payload, token })
}

export function updateProvider(tenantId: string, providerId: string, payload: IdpWrite, token: string) {
  return apiFetch<IdpOut>(`/auth/sso/admin/${tenantId}/providers/${providerId}`, { method: "PATCH", body: payload, token })
}

export function deleteProvider(tenantId: string, providerId: string, token: string) {
  return apiFetch<void>(`/auth/sso/admin/${tenantId}/providers/${providerId}`, { method: "DELETE", token })
}
