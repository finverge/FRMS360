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
  member_count: number
  // "seeded" for an unedited copy of the platform catalogue, "custom" for a
  // tenant-created role or one the tenant has since edited. null for a role still
  // served from the hardcoded fallback (no tenant_roles row exists at all yet).
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
