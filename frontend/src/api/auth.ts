/**
 * Every shape here mirrors a real Pydantic schema in services/tenant_service/app/schemas.py
 * and route in services/tenant_service/app/routes/{auth,sso,tenants}.py - verified against
 * the source, not assumed, so the frontend contract cannot silently drift from the API it
 * actually calls.
 */
import { apiFetch } from "./client"

export interface TokenOut {
  access_token: string
  token_type: string
  role: string
  tenant_id: string | null
  must_change_password: boolean
  refresh_token?: string | null
  expires_in?: number | null
  mfa_required: boolean
  mfa_enrolment_required: boolean
}

export function login(email: string, password: string) {
  return apiFetch<TokenOut>("/auth/login", { method: "POST", body: { email, password } })
}

export function verifyMfa(code: string, pendingToken: string) {
  return apiFetch<TokenOut>("/auth/mfa/verify", {
    method: "POST",
    body: { code },
    token: pendingToken,
  })
}

export interface MfaEnrolOut {
  secret: string
  otpauth_uri: string
  qr_svg: string
  issuer: string
  account: string
}

export function startMfaEnrolment(pendingToken: string) {
  return apiFetch<MfaEnrolOut>("/auth/mfa/enrol", { method: "POST", token: pendingToken })
}

export interface MfaActivateOut {
  enabled: boolean
  backup_codes: string[]
  notice: string
}

export function activateMfa(code: string, pendingToken: string) {
  return apiFetch<MfaActivateOut>("/auth/mfa/activate", {
    method: "POST",
    body: { code },
    token: pendingToken,
  })
}

export function changePassword(currentPassword: string, newPassword: string, token: string) {
  return apiFetch<TokenOut>("/auth/change-password", {
    method: "POST",
    body: { current_password: currentPassword, new_password: newPassword },
    token,
  })
}

export function passwordPolicy() {
  return apiFetch<{ policy: string }>("/auth/password-policy")
}

export interface SsoProvider {
  slug: string
  display_name: string
  protocol: string
}

export interface SsoLookupOut {
  tenant: string
  display_name?: string
  providers: SsoProvider[]
}

export function lookupSso(tenantSlug: string) {
  return apiFetch<SsoLookupOut>(`/sso/providers/${encodeURIComponent(tenantSlug)}`)
}

/** Not a fetch - a full-page redirect into the IdP, exactly like the current console. */
export function ssoStartUrl(tenantSlug: string, idpSlug: string) {
  return `/api/sso/${encodeURIComponent(tenantSlug)}/${encodeURIComponent(idpSlug)}/start`
}

export type EntityType =
  | "commercial_bank"
  | "aifi"
  | "urban_cooperative"
  | "state_cooperative"
  | "central_cooperative"
  | "rrb"
  | "local_area_bank"
  | "small_finance_bank"
  | "payments_bank"
  | "nbfc"
  | "hfc"

export interface SelfServiceSignup {
  slug: string
  legal_name: string
  display_name: string
  entity_type: EntityType
  admin_email: string
  admin_password: string
  ucb_tier?: number
}

export interface TenantOut {
  id: string
  slug: string
  legal_name: string
  display_name: string
  region: string
  plan: string
  entity_type: string
  ucb_tier: number | null
  status: string
  is_sandbox: boolean
  created_at: string
}

/** BR-109: no token - this is the one intentionally public tenant-mutating route. */
export function requestSandbox(payload: SelfServiceSignup) {
  return apiFetch<TenantOut>("/tenants/self-service", { method: "POST", body: payload })
}
