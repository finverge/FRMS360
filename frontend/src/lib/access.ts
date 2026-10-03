// What a role may open, decided only from what the server reports for it, in one place, so the sidebar, the Home page and the monitoring tabs
// cannot drift apart. Everything is derived from GET /auth/me (api/auth.ts), which the
// server resolves the way every enforcing service does - the console does not carry its own
// idea of the role table. This only decides what is RENDERED; every endpoint re-checks.
import type { MeOut } from "@/api/auth"

export type SectionId =
  | "home" | "monitoring" | "sanctions" | "lanec"
  | "branding" | "users" | "roles" | "idp" | "notifications" | "configs"

const hasModule = (me: MeOut, key: string) => me.modules.some((m) => m.key === key)

/** Whether this role's own row grants a gated action (cp_common.permissions). */
export const hasPermission = (me: MeOut, key: string) => me.permissions.includes(key)

/** Why each rule is what it is - kept next to the rule, because "who sees this" is the question
 * an inspection asks. */
const RULES: Record<SectionId, (me: MeOut) => boolean> = {
  home: () => true,
  // Dashboards sit inside the Monitoring module; a role with the module but no dashboards
  // would see an empty page, so it needs at least one.
  monitoring: (me) => hasModule(me, "monitoring") && me.dashboards.length > 0,
  // Both are gated by permissions on the role, and the servers enforce the same ones (a
  // monitoring-module role is not enough: Board and Model Risk see neither unless granted).
  sanctions: (me) => hasModule(me, "monitoring") && hasPermission(me, "sanctions.screen"),
  lanec: (me) => hasModule(me, "monitoring") && hasPermission(me, "lane_c.view"),
  // The tenant-administration pages all act on the tenant itself.
  branding: (me) => me.can_admin_tenant,
  users: (me) => me.can_admin_tenant,
  roles: (me) => me.can_admin_tenant,
  idp: (me) => me.can_admin_tenant,
  notifications: (me) => me.can_admin_tenant,
  configs: (me) => me.can_admin_tenant,
}

export function canOpen(me: MeOut, section: SectionId): boolean {
  return RULES[section](me)
}

export function canOpenDashboard(me: MeOut, key: string): boolean {
  return hasModule(me, "monitoring") && me.dashboards.some((d) => d.key === key)
}

/** The dashboard a role lives in: the first of its dashboards. The server lists them in the
 * order the role's own row gives, so an administrator chooses a role's home dashboard by
 * ordering it - the console holds no opinion about any role by name. */
export function primaryDashboard(me: MeOut): string | null {
  return me.dashboards[0]?.key ?? null
}

/** What the person is shown about their role: the description an administrator wrote on it,
 * or, if there is none, what its dashboards are for. */
export function roleText(me: MeOut): string {
  if (me.description) return me.description
  const q = me.dashboards.slice(0, 2).map((d) => d.question).join(" ")
  return q ? `${me.role_label}. ${q}` : `${me.role_label}. Nothing has been enabled for this role yet; ask your administrator.`
}

export function scopeText(me: MeOut, tenantName: string | null): string {
  const reach = me.tenant_scoped
    ? `You see only ${tenantName ?? "your bank"}.`
    : "You can act across every tenant."
  const pii = me.can_reveal_pii
    ? "You can unmask customer identifiers, with a justification that is recorded."
    : "Customer identifiers always stay masked for your role."
  return `${reach} ${pii}`
}
