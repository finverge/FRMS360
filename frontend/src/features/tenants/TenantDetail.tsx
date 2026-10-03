import { useState } from "react"
import {
  Activity, Fingerprint, Landmark, LayoutDashboard, Palette, Search, Settings2, ShieldCheck,
  Users as UsersIcon,
} from "lucide-react"

import type { TenantOut } from "@/api/tenants"
import { useSession } from "@/app/SessionContext"
import { canOpen } from "@/lib/access"
import { TenantHeader } from "./TenantHeader"
import { BrandingPanel } from "./BrandingPanel"
import { UsersPanel } from "./UsersPanel"
import { DetectionConfigsPanel } from "./configs/DetectionConfigsPanel"
import { RolesPanel } from "./roles/RolesPanel"
import { IdentityProvidersPanel } from "./idp/IdentityProvidersPanel"
import { NotificationsPanel } from "./notifications/NotificationsPanel"
import { MonitoringPanel } from "./monitoring/MonitoringPanel"
import { SanctionsScreeningPanel } from "./monitoring/SanctionsScreeningPanel"
import { LaneCPanel } from "./monitoring/LaneCPanel"

// "home" is not here: it is not a tenant panel (it is rendered by ConsoleApp, and for a
// platform administrator it exists before any tenant is chosen).
export type TenantSection =
  | "monitoring" | "sanctions" | "lanec" | "branding" | "users" | "roles" | "idp" | "notifications" | "configs"

// Single source of truth the sidebar builds its "Tenant Admin" section from - the same
// shape Mandate360's WORKSPACE_SECTIONS/MANDATE360_SECTIONS export, so App-level nav and
// the section this component actually renders can never silently drift apart.
export const TENANT_SECTIONS: {
  value: TenantSection
  label: string
  icon: React.ComponentType<{ className?: string }>
  group: "monitoring" | "admin"
}[] = [
  { value: "monitoring", label: "Monitoring", icon: LayoutDashboard, group: "monitoring" },
  { value: "sanctions", label: "Sanctions Screening", icon: Search, group: "monitoring" },
  { value: "lanec", label: "Borrower Credit Health", icon: Landmark, group: "monitoring" },
  { value: "branding", label: "Branding", icon: Palette, group: "admin" },
  { value: "users", label: "Users", icon: UsersIcon, group: "admin" },
  { value: "roles", label: "Roles", icon: ShieldCheck, group: "admin" },
  { value: "idp", label: "Identity providers", icon: Fingerprint, group: "admin" },
  { value: "notifications", label: "Notifications", icon: Activity, group: "admin" },
  { value: "configs", label: "Detection configs", icon: Settings2, group: "admin" },
]

export function TenantDetail({
  tenant: initialTenant,
  section,
  initialDashboard,
  onTenantChanged,
}: {
  tenant: TenantOut
  section: TenantSection
  initialDashboard?: string | null
  onTenantChanged: (tenant: TenantOut) => void
}) {
  const { me } = useSession()
  const allowed = canOpen(me, section)
  const [tenant, setTenant] = useState(initialTenant)

  function handleChanged(t: TenantOut) {
    setTenant(t)
    onTenantChanged(t)
  }

  return (
    <>
      <TenantHeader tenant={tenant} onChanged={handleChanged} />
      {/* The menu already hides what the role cannot open; this is the same rule applied
          again here so a stale selection can never render a page the server will refuse. */}
      {!allowed && (
        <p className="text-sm text-muted-foreground">Your role has no access to this page.</p>
      )}
      {allowed && section === "monitoring" && <MonitoringPanel tenantId={tenant.id} initialDashboard={initialDashboard} />}
      {allowed && section === "sanctions" && <SanctionsScreeningPanel tenantId={tenant.id} />}
      {allowed && section === "lanec" && <LaneCPanel tenantId={tenant.id} />}
      {allowed && section === "branding" && <BrandingPanel tenantId={tenant.id} />}
      {allowed && section === "users" && <UsersPanel tenantId={tenant.id} />}
      {allowed && section === "roles" && <RolesPanel tenantId={tenant.id} />}
      {allowed && section === "idp" && <IdentityProvidersPanel tenantId={tenant.id} />}
      {allowed && section === "notifications" && <NotificationsPanel tenantId={tenant.id} />}
      {allowed && section === "configs" && <DetectionConfigsPanel tenantId={tenant.id} />}
    </>
  )
}
