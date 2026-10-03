import { useEffect, useState } from "react"
import { Building2, Home, LogOut, Moon, Plus, Search, Sun } from "lucide-react"

import type { Session } from "@/features/auth/AuthPage"
import { getMe, type MeOut } from "@/api/auth"
import { listTenants, type TenantOut } from "@/api/tenants"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import {
  ContentBody, Sidebar, SidebarFooter, SidebarHeader, SidebarMain, SidebarNav,
  SidebarNavItem, SidebarSectionLabel, SidebarShell, UserAvatar,
} from "@/components/ui/sidebar"
import { useTheme } from "./ThemeContext"
import { SessionProvider, useSession } from "./SessionContext"
import { TenantDetail, TENANT_SECTIONS } from "@/features/tenants/TenantDetail"
import { OnboardTenantDialog } from "@/features/tenants/OnboardTenantDialog"
import { HomePage } from "@/features/home/HomePage"
import { canOpen, type SectionId } from "@/lib/access"

function ConsoleBody() {
  const { accessToken, isPlatformAdmin, me, theme, signOut, toggleTheme } = useConsoleSession()
  const [tenants, setTenants] = useState<TenantOut[] | null>(null)
  const [selected, setSelected] = useState<TenantOut | null>(null)
  // Everyone lands on Home; what it shows, and what the sidebar offers, comes from the role.
  const [section, setSection] = useState<SectionId>("home")
  const [dashboard, setDashboard] = useState<string | null>(null)
  const [onboardOpen, setOnboardOpen] = useState(false)
  const [reloadToken, setReloadToken] = useState(0)
  const [tenantQuery, setTenantQuery] = useState("")

  useEffect(() => {
    listTenants(accessToken).then(setTenants).catch(() => setTenants([]))
  }, [accessToken, reloadToken])

  // A tenant-scoped role only ever has one tenant to see - auto-select it and skip
  // showing a picker at all, the same "single eligible option needs no picker" choice
  // Mandate360's own product-picker makes for a single-eligible-role sign-in.
  useEffect(() => {
    if (!isPlatformAdmin && tenants && tenants.length === 1 && !selected) {
      setSelected(tenants[0])
    }
  }, [isPlatformAdmin, tenants, selected])

  function selectTenant(t: TenantOut) {
    setSelected(t)
    setSection("home")
    setDashboard(null)
  }

  function openSection(s: SectionId) {
    setSection(s)
    setDashboard(null)
  }

  // A Home tile or card opens a specific dashboard, not just the Monitoring page.
  function openDashboard(key: string) {
    setSection("monitoring")
    setDashboard(key)
  }

  return (
    <SidebarShell>
      <Sidebar>
        <SidebarHeader>
          <img src="/logo-mark.png" alt="" className="size-6" />
          <span className="text-sm font-semibold tracking-tight">Fraud360 Control Plane</span>
        </SidebarHeader>

        <SidebarNav>
          {isPlatformAdmin && (
            <div>
              <div className="flex items-center justify-between px-2">
                <SidebarSectionLabel className="px-0 pt-0">Tenants</SidebarSectionLabel>
                <button
                  type="button"
                  onClick={() => setOnboardOpen(true)}
                  aria-label="Onboard a tenant"
                  className="rounded-md p-1 text-sidebar-foreground/60 hover:bg-sidebar-accent hover:text-sidebar-accent-foreground"
                >
                  <Plus className="size-3.5" />
                </button>
              </div>
              {tenants && tenants.length > 8 && (
                <div className="relative px-2 pb-1">
                  <Search className="pointer-events-none absolute top-1/2 left-4 size-3.5 -translate-y-1/2 text-sidebar-foreground/40" />
                  <Input
                    value={tenantQuery}
                    onChange={(e) => setTenantQuery(e.target.value)}
                    placeholder="Search tenants"
                    className="h-7 border-sidebar-border bg-sidebar-accent/20 pl-7 text-xs text-sidebar-foreground placeholder:text-sidebar-foreground/40 focus-visible:ring-sidebar-accent"
                  />
                </div>
              )}
              <div className="mt-1 space-y-0.5">
                {tenants === null && (
                  <p className="px-2.5 py-1.5 text-sm text-sidebar-foreground/60">Loading…</p>
                )}
                {tenants?.length === 0 && (
                  <p className="px-2.5 py-1.5 text-sm text-sidebar-foreground/60">No tenants yet.</p>
                )}
                {tenants && tenants.length > 0 && (() => {
                  const q = tenantQuery.trim().toLowerCase()
                  const filteredTenants = q ? tenants.filter((t) => t.display_name.toLowerCase().includes(q)) : tenants
                  if (filteredTenants.length === 0) {
                    return <p className="px-2.5 py-1.5 text-sm text-sidebar-foreground/60">No tenants match "{tenantQuery.trim()}".</p>
                  }
                  return filteredTenants.map((t) => (
                  <SidebarNavItem
                    key={t.id}
                    icon={Building2}
                    active={selected?.id === t.id}
                    onClick={() => selectTenant(t)}
                  >
                    {t.display_name}
                    {t.is_sandbox && (
                      <span className="ml-1.5 text-xs font-normal text-sidebar-foreground/50">sandbox</span>
                    )}
                  </SidebarNavItem>
                  ))
                })()}
              </div>
            </div>
          )}

          <div>
            <SidebarNavItem icon={Home} active={section === "home"} onClick={() => openSection("home")}>
              Home
            </SidebarNavItem>
          </div>

          {selected &&
            (["monitoring", "admin"] as const).map((group) => {
              const items = TENANT_SECTIONS.filter((s) => s.group === group && canOpen(me, s.value))
              if (items.length === 0) return null
              return (
                <div key={group}>
                  <SidebarSectionLabel>{group === "monitoring" ? "Monitoring" : "Tenant Admin"}</SidebarSectionLabel>
                  {items.map((s) => (
                    <SidebarNavItem key={s.value} icon={s.icon} active={section === s.value} onClick={() => openSection(s.value)}>
                      {s.label}
                    </SidebarNavItem>
                  ))}
                </div>
              )
            })}
        </SidebarNav>

        <SidebarFooter>
          <div className="flex items-center gap-2.5 px-1 py-1.5">
            <UserAvatar name={me.role_label} />
            <div className="min-w-0 flex-1">
              <Badge variant="outline" className="uppercase tracking-wide">{me.role_label}</Badge>
            </div>
            <Button
              variant="ghost" size="icon"
              onClick={toggleTheme}
              aria-label={theme === "dark" ? "Switch to light theme" : "Switch to dark theme"}
            >
              {theme === "dark" ? <Sun className="size-4" /> : <Moon className="size-4" />}
            </Button>
          </div>
          <Button variant="ghost" size="sm" className="w-full justify-start" onClick={signOut}>
            <LogOut className="size-4" />
            Sign out
          </Button>
        </SidebarFooter>
      </Sidebar>

      <SidebarMain>
        <ContentBody>
          {section === "home" ? (
            // A tenant-scoped role's tenant is auto-selected, so until it arrives there is
            // nothing to summarise; a platform administrator gets the platform view instead.
            isPlatformAdmin || selected ? (
              <HomePage
                key={selected?.id ?? "fleet"}
                tenant={selected}
                tenants={tenants}
                onOpenDashboard={openDashboard}
                onOpenSection={openSection}
                onOpenTenant={selectTenant}
                onOnboard={() => setOnboardOpen(true)}
              />
            ) : (
              <div className="flex h-full items-center justify-center text-muted-foreground">Loading your tenant…</div>
            )
          ) : selected ? (
            <TenantDetail
              key={selected.id + (dashboard ?? "")}
              tenant={selected}
              section={section}
              initialDashboard={dashboard}
              onTenantChanged={setSelected}
            />
          ) : null}
        </ContentBody>
      </SidebarMain>

      <OnboardTenantDialog
        open={onboardOpen}
        onOpenChange={setOnboardOpen}
        onCreated={(tenant) => {
          setSelected(tenant)
          setReloadToken((n) => n + 1)
        }}
      />
    </SidebarShell>
  )
}

// Small adapter so ConsoleBody reads session + theme through one call - both contexts
// are mounted right above it either way, this just keeps the component body flat.
function useConsoleSession() {
  const session = useSession()
  const { theme, toggleTheme } = useTheme()
  return { ...session, theme, toggleTheme }
}

export function ConsoleApp({ session, signOut }: { session: Session; signOut: () => void }) {
  const [me, setMe] = useState<MeOut | null>(null)

  // The console renders from what the server says this role may use. If it cannot say (an
  // expired or refused token), the person goes back to sign in rather than being shown a menu
  // the console invented for them.
  useEffect(() => {
    let live = true
    getMe(session.accessToken)
      .then((m) => { if (live) setMe(m) })
      .catch(() => { if (live) signOut() })
    return () => { live = false }
  }, [session.accessToken, signOut])

  if (!me) {
    return <div className="flex h-svh items-center justify-center text-muted-foreground">Loading…</div>
  }
  return (
    <SessionProvider session={session} me={me} signOut={signOut}>
      <ConsoleBody />
    </SessionProvider>
  )
}
