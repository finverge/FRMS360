/**
 * Verified against services/tenant_service/app/routes/tenants.py and schemas.py -
 * every shape and route here is a real one, not assumed.
 */
import { apiFetch } from "./client"
import type { EntityType, TenantOut } from "./auth"

export type { TenantOut }

export interface TenantCreate {
  slug: string
  legal_name: string
  display_name: string
  region?: string
  plan?: string
  entity_type: EntityType
  ucb_tier?: number | null
  admin_email: string
  admin_password: string
  primary_color?: string
  accent_color?: string
}

export function listTenants(token: string) {
  return apiFetch<TenantOut[]>("/tenants", { token })
}

export function createTenant(payload: TenantCreate, token: string) {
  return apiFetch<TenantOut>("/tenants", { method: "POST", body: payload, token })
}

export function getTenant(tenantId: string, token: string) {
  return apiFetch<TenantOut>(`/tenants/${tenantId}`, { token })
}

export type LifecycleAction = "suspend" | "resume" | "offboard"

export function transitionTenant(tenantId: string, action: LifecycleAction, token: string) {
  return apiFetch<TenantOut>(`/tenants/${tenantId}/${action}`, { method: "POST", token })
}

/** BR-109: sandbox -> real, billable tenant. platform_admin only, and only once. */
export function promoteTenant(tenantId: string, token: string) {
  return apiFetch<TenantOut>(`/tenants/${tenantId}/promote`, { method: "POST", token })
}

export function exportTenant(tenantId: string, token: string) {
  return apiFetch<Record<string, unknown>>(`/tenants/${tenantId}/export`, { token })
}

// ---- users ----
export interface TenantUserOut {
  id: string
  email: string
  role: string
  created_at: string
}

export interface UserInviteResult extends TenantUserOut {
  temp_password: string | null
  emailed: boolean
}

export function listUsers(tenantId: string, token: string) {
  return apiFetch<TenantUserOut[]>(`/tenants/${tenantId}/users`, { token })
}

export function inviteUser(tenantId: string, email: string, role: string, token: string) {
  return apiFetch<UserInviteResult>(`/tenants/${tenantId}/users`, {
    method: "POST",
    body: { email, role },
    token,
  })
}

export function updateUserRole(tenantId: string, userId: string, role: string, token: string) {
  return apiFetch<TenantUserOut>(`/tenants/${tenantId}/users/${userId}`, {
    method: "PUT",
    body: { role },
    token,
  })
}

export function removeUser(tenantId: string, userId: string, token: string) {
  return apiFetch<void>(`/tenants/${tenantId}/users/${userId}`, { method: "DELETE", token })
}

// ---- roles (BR-112 read, BR-113 write path) ----
export interface CatalogueItem {
  key: string
  label: string
  [k: string]: unknown
}

export interface RoleOut {
  name: string
  label: string
  modules: CatalogueItem[]
  dashboards: CatalogueItem[]
  can_admin_tenant: boolean
  can_reveal_pii: boolean
  can_activate_config: boolean
  // Gated actions this role may perform (keys from the permission catalogue below), and the
  // text its holders see on their home page. Both are the tenant's own data.
  permissions: string[]
  description: string
  member_count: number
  // "seeded" for an unedited copy of the starter template, "custom" for a role the tenant
  // created or has since edited. Every role is a row the tenant owns; none is built in.
  source: string | null
  // True while a can_admin_tenant/can_reveal_pii/can_activate_config grant is staged
  // awaiting a second, different eligible actor's confirmation (BR-715-style
  // maker-checker - see roles.py's update_role/confirm_elevation).
  elevation_pending: boolean
}

export interface RoleWrite {
  label?: string
  modules?: string[]
  dashboards?: string[]
  can_admin_tenant?: boolean
  can_reveal_pii?: boolean
  can_activate_config?: boolean
  permissions?: string[]
  description?: string
}

/** What a role can be granted. The vocabulary only - who holds what is each role's data. */
export interface PermissionCatalogue {
  permissions: { key: string; label: string; group: string }[]
  modules: { key: string; label: string; description: string }[]
  dashboards: { key: string; label: string; persona: string; question: string }[]
}

export function getPermissionCatalogue(tenantId: string, token: string) {
  return apiFetch<PermissionCatalogue>(`/tenants/${tenantId}/permissions`, { token })
}

export function listRoles(tenantId: string, token: string) {
  return apiFetch<RoleOut[]>(`/tenants/${tenantId}/roles`, { token })
}

export function createRole(
  tenantId: string,
  payload: RoleWrite & { name: string; label: string },
  token: string
) {
  return apiFetch<RoleOut>(`/tenants/${tenantId}/roles`, { method: "POST", body: payload, token })
}

export function updateRole(tenantId: string, name: string, payload: RoleWrite, token: string) {
  return apiFetch<RoleOut>(`/tenants/${tenantId}/roles/${name}`, { method: "PUT", body: payload, token })
}

export function confirmRoleElevation(tenantId: string, name: string, token: string) {
  return apiFetch<RoleOut>(`/tenants/${tenantId}/roles/${name}/confirm`, { method: "POST", token })
}

export function deleteRole(tenantId: string, name: string, token: string) {
  return apiFetch<void>(`/tenants/${tenantId}/roles/${name}`, { method: "DELETE", token })
}
