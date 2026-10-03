import { useEffect, useMemo, useState, type ReactNode } from "react"
import { ArrowRight, Building2, CalendarDays, LayoutDashboard, Plus, ShieldCheck } from "lucide-react"

import type { MeOut, TenantOut } from "@/api/auth"
import { getDashboard, type DashboardOut } from "@/api/dashboards"
import { useSession } from "@/app/SessionContext"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { canOpen, canOpenDashboard, primaryDashboard, roleText, scopeText, type SectionId } from "@/lib/access"
import { TENANT_SECTIONS } from "@/features/tenants/TenantDetail"
import { BAD_IF_NONZERO, formatMetric } from "@/features/tenants/monitoring/format"
import { isoDaysAgo } from "@/features/tenants/monitoring/useDashboard"
import { RENDERED_DASHBOARDS } from "@/features/tenants/monitoring/catalogue"
import { pickSources, QUEUES } from "./homeQueues"

// What Home shows is decided by the same rules as the sidebar (lib/access.ts): a block appears
// only if the role may open the page behind it, and a figure only if the role may open the
// dashboard it comes from. Home never shows a number the person cannot click through to.

const greeting = () => {
  const h = new Date().getHours()
  return h < 12 ? "Good morning" : h < 17 ? "Good afternoon" : "Good evening"
}

function Block({ title, hint, children }: { title: string; hint?: string; children: ReactNode }) {
  return (
    <section className="mb-8">
      <div className="mb-3">
        <h2 className="text-base font-semibold">{title}</h2>
        {hint && <p className="text-sm text-muted-foreground">{hint}</p>}
      </div>
      {children}
    </section>
  )
}

/** The dashboards behind this role's queues and headline figures, fetched once, in parallel.
 * The window is the last 30 days, the same default every dashboard opens on. */
function useHomeData(tenantId: string | null, me: MeOut, token: string) {
  const plan = useMemo(() => pickSources(me), [me])
  const [data, setData] = useState<Record<string, DashboardOut> | null>(null)
  const [failed, setFailed] = useState(0)

  useEffect(() => {
    if (!tenantId) {
      setData({})
      return
    }
    let live = true
    setData(null)
    const filters = { date_from: isoDaysAgo(30), date_to: new Date().toISOString() }
    Promise.allSettled(plan.dashboards.map((d) => getDashboard(tenantId, d, filters, token))).then((results) => {
      if (!live) return
      const out: Record<string, DashboardOut> = {}
      let bad = 0
      results.forEach((r, i) => {
        if (r.status === "fulfilled") out[plan.dashboards[i]] = r.value
        else bad += 1
      })
      setData(out)
      setFailed(bad)
    })
    return () => {
      live = false
    }
  }, [tenantId, plan, token])

  return { plan, data, failed }
}

function Tile({ label, value, hint, tone, onClick }: { label: string; value: string; hint?: string; tone?: "bad" | "good"; onClick?: () => void }) {
  const body = (
    <>
      <div className="text-xs font-medium tracking-wide text-muted-foreground uppercase">{label}</div>
      <div className={"text-2xl font-semibold " + (tone === "bad" ? "text-destructive" : tone === "good" ? "text-success" : "")}>{value}</div>
      {hint && <div className="text-xs text-muted-foreground">{hint}</div>}
    </>
  )
  const base = "rounded-lg border border-border bg-card p-4 text-left shadow-sm"
  return onClick ? (
    <button type="button" onClick={onClick} className={base + " transition hover:-translate-y-0.5 hover:shadow-md focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none"}>
      {body}
    </button>
  ) : (
    <div className={base}>{body}</div>
  )
}

function Queues({ me, plan, data, open }: { me: MeOut; plan: ReturnType<typeof pickSources>; data: Record<string, DashboardOut>; open: (d: string) => void }) {
  const active = QUEUES.flatMap((q) => {
    const source = plan.queueSource.get(q.metric)
    const value = source ? data[source]?.metrics[q.metric]?.value : undefined
    return source && value && value > 0 && canOpenDashboard(me, source) ? [{ q, source, value }] : []
  })
  return (
    <Block
      title="Waiting for you"
      hint={active.length ? "Only work in the areas your role can open is listed. Click one to start." : "Nothing is waiting for your role right now."}
    >
      <ul className="grid gap-3 md:grid-cols-2">
        {active.map(({ q, source, value }) => {
          const hot = q.tone === "alert"
          return (
            <li key={q.metric}>
              <button
                type="button"
                onClick={() => open(source)}
                className="group flex w-full items-center gap-4 rounded-xl border border-border bg-card p-4 text-left shadow-sm transition hover:-translate-y-0.5 hover:shadow-md focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none"
                style={{ borderLeftWidth: 4, borderLeftColor: hot ? "var(--destructive)" : "var(--warning)" }}
              >
                <span
                  className={"flex h-12 min-w-12 items-center justify-center rounded-lg px-2 text-2xl font-bold tabular-nums " + (hot ? "text-destructive" : "text-warning")}
                  style={{ background: `color-mix(in srgb, ${hot ? "var(--destructive)" : "var(--warning)"} 14%, transparent)` }}
                >
                  {value.toLocaleString("en-IN")}
                </span>
                <span className="min-w-0 flex-1">
                  <span className="block text-sm font-medium">{q.label}</span>
                  <span className="block text-xs text-muted-foreground">{q.why}</span>
                </span>
                <ArrowRight className="size-4 shrink-0 text-muted-foreground transition group-hover:translate-x-1" aria-hidden />
              </button>
            </li>
          )
        })}
      </ul>
    </Block>
  )
}

function Headlines({ me, data, open }: { me: MeOut; data: Record<string, DashboardOut>; open: (d: string) => void }) {
  const key = primaryDashboard(me)
  const dash = key ? data[key] : undefined
  const meta = me.dashboards.find((d) => d.key === key)
  if (!key || !dash || !meta) return null
  const tiles = Object.values(dash.metrics).slice(0, 6)
  return (
    <Block title="At a glance" hint={`Last 30 days. The ${meta.label} dashboard has the breakdowns and records behind these.`}>
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-3">
        {tiles.map((m) => (
          <Tile
            key={m.name}
            label={m.label}
            value={formatMetric(m)}
            tone={BAD_IF_NONZERO.has(m.name) ? (m.value > 0 ? "bad" : "good") : undefined}
            onClick={() => open(key)}
          />
        ))}
      </div>
      <button type="button" className="mt-3 inline-flex items-center gap-1 text-sm text-primary hover:underline" onClick={() => open(key)}>
        Open the {meta.label} dashboard <ArrowRight className="size-3.5" aria-hidden />
      </button>
    </Block>
  )
}

const STATUS_VARIANT: Record<string, "success" | "secondary" | "destructive" | "outline"> = {
  active: "success", provisioning: "secondary", degraded: "destructive", suspended: "destructive", offboarded: "outline",
}

/** A platform administrator's own view: the banks on the platform, not any one bank's queue. */
function Fleet({ tenants, onOpenTenant, onOnboard }: { tenants: TenantOut[] | null; onOpenTenant: (t: TenantOut) => void; onOnboard: () => void }) {
  const counts = useMemo(() => {
    const c: Record<string, number> = {}
    for (const t of tenants ?? []) c[t.status] = (c[t.status] ?? 0) + 1
    return c
  }, [tenants])
  return (
    <>
      <Block title="The platform" hint="Banks on the platform, by state. Open one to see its dashboards and settings.">
        <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
          <Tile label="Tenants" value={String(tenants?.length ?? 0)} />
          <Tile label="Active" value={String(counts.active ?? 0)} tone="good" />
          <Tile label="Suspended or degraded" value={String((counts.suspended ?? 0) + (counts.degraded ?? 0))} tone={(counts.suspended ?? 0) + (counts.degraded ?? 0) > 0 ? "bad" : undefined} />
          <Tile label="Sandboxes" value={String((tenants ?? []).filter((t) => t.is_sandbox).length)} />
        </div>
      </Block>
      <Block title="Tenants">
        {tenants === null && <p className="text-sm text-muted-foreground">Loading…</p>}
        {tenants?.length === 0 && (
          <div className="rounded-lg border border-dashed border-border p-6 text-sm text-muted-foreground">
            No tenants yet.{" "}
            <Button size="sm" variant="outline" className="ml-2" onClick={onOnboard}><Plus className="size-3.5" />Onboard a tenant</Button>
          </div>
        )}
        <div className="grid gap-3 md:grid-cols-2 lg:grid-cols-3">
          {tenants?.map((t) => (
            <button key={t.id} type="button" onClick={() => onOpenTenant(t)}
              className="flex items-center gap-3 rounded-xl border border-border bg-card p-4 text-left shadow-sm transition hover:-translate-y-0.5 hover:shadow-md focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none">
              <span className="flex size-9 shrink-0 items-center justify-center rounded-lg bg-primary/10 text-primary"><Building2 className="size-4" aria-hidden /></span>
              <span className="min-w-0 flex-1">
                <span className="block truncate text-sm font-medium">{t.display_name}</span>
                <span className="block truncate text-xs text-muted-foreground">{t.plan}{t.is_sandbox ? " · sandbox" : ""}</span>
              </span>
              <Badge variant={STATUS_VARIANT[t.status] ?? "outline"}>{t.status}</Badge>
            </button>
          ))}
        </div>
      </Block>
    </>
  )
}

function WhereYouCanGo({ me, openDashboard, openSection }: { me: MeOut; openDashboard: (d: string) => void; openSection: (s: SectionId) => void }) {
  const dashboards = me.dashboards.filter((d) => RENDERED_DASHBOARDS.has(d.key))
  const tools = TENANT_SECTIONS.filter((s) => s.group === "monitoring" && s.value !== "monitoring" && canOpen(me, s.value))
  const admin = TENANT_SECTIONS.filter((s) => s.group === "admin" && canOpen(me, s.value))
  return (
    <Block title="Where you can go" hint="These are the pages open to your role.">
      <div className="grid gap-3 md:grid-cols-2 lg:grid-cols-3">
        {dashboards.length > 0 && (
          <div className="rounded-xl border border-border bg-card p-4 shadow-sm md:col-span-2 lg:col-span-3">
            <div className="mb-3 flex items-center gap-3">
              <span className="flex size-9 items-center justify-center rounded-lg bg-primary/10 text-primary"><LayoutDashboard className="size-[18px]" aria-hidden /></span>
              <p className="text-sm font-medium">Monitoring dashboards</p>
            </div>
            <ul className="grid gap-2 md:grid-cols-2 lg:grid-cols-3">
              {dashboards.map((d) => (
                <li key={d.key}>
                  <button type="button" onClick={() => openDashboard(d.key)}
                    className="w-full rounded-lg border border-border px-3 py-2 text-left transition hover:border-primary/40 hover:bg-accent focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none">
                    <span className="block text-sm font-medium">{d.label}</span>
                    <span className="block text-xs text-muted-foreground">{d.question}</span>
                  </button>
                </li>
              ))}
            </ul>
          </div>
        )}
        {[{ title: "Screening and credit", items: tools }, { title: "Tenant administration", items: admin }].map((g) =>
          g.items.length === 0 ? null : (
            <div key={g.title} className="rounded-xl border border-border bg-card p-4 shadow-sm">
              <p className="mb-3 text-sm font-medium">{g.title}</p>
              <div className="flex flex-wrap gap-2">
                {g.items.map((s) => (
                  <button key={s.value} type="button" onClick={() => openSection(s.value)}
                    className="inline-flex items-center gap-1.5 rounded-full border border-border px-3 py-1 text-xs transition hover:border-primary/40 hover:bg-accent focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none">
                    <s.icon className="size-3.5" />{s.label}
                  </button>
                ))}
              </div>
            </div>
          ),
        )}
      </div>
    </Block>
  )
}

export function HomePage({ tenant, tenants, onOpenDashboard, onOpenSection, onOpenTenant, onOnboard }: {
  tenant: TenantOut | null
  tenants: TenantOut[] | null
  onOpenDashboard: (key: string) => void
  onOpenSection: (s: SectionId) => void
  onOpenTenant: (t: TenantOut) => void
  onOnboard: () => void
}) {
  const { me, accessToken, isPlatformAdmin } = useSession()
  const { plan, data, failed } = useHomeData(tenant?.id ?? null, me, accessToken)
  const showFleet = isPlatformAdmin && !tenant
  const nothingEnabled = me.modules.length === 0 && me.dashboards.length === 0 && !me.can_admin_tenant
  return (
    <>
      <div className="mb-8 overflow-hidden rounded-2xl bg-gradient-to-br from-primary to-primary/70 p-6 text-primary-foreground shadow-md">
        <div className="flex flex-wrap items-center gap-2 text-xs opacity-90">
          <span className="inline-flex items-center gap-1 rounded-full bg-white/15 px-2.5 py-1">
            <CalendarDays className="size-3.5" aria-hidden />
            {new Date().toLocaleDateString("en-IN", { weekday: "long", day: "numeric", month: "long", year: "numeric" })}
          </span>
          <span className="inline-flex items-center gap-1 rounded-full bg-white/15 px-2.5 py-1"><ShieldCheck className="size-3.5" aria-hidden />{me.role_label}</span>
          {tenant && <span className="inline-flex items-center gap-1 rounded-full bg-white/15 px-2.5 py-1"><Building2 className="size-3.5" aria-hidden />{tenant.display_name}</span>}
        </div>
        <h2 className="mt-3 text-2xl font-semibold">{greeting()}, {me.subject}</h2>
        <p className="mt-1 max-w-2xl text-sm opacity-90">{roleText(me)}</p>
        <p className="mt-2 text-xs opacity-75">{scopeText(me, tenant?.display_name ?? null)}</p>
      </div>

      {nothingEnabled && (
        <div className="mb-8 rounded-lg border border-border bg-muted p-4 text-sm">
          No pages have been enabled for your role yet. Ask your administrator to review it under Roles.
        </div>
      )}

      {showFleet && <Fleet tenants={tenants} onOpenTenant={onOpenTenant} onOnboard={onOnboard} />}

      {tenant && data === null && <p className="mb-8 text-sm text-muted-foreground">Loading your figures…</p>}
      {tenant && data && (
        <>
          {failed > 0 && (
            <p className="mb-4 text-sm text-warning" role="status">
              {failed === plan.dashboards.length ? "Your figures could not be loaded." : "Some of your figures could not be loaded."} The dashboards themselves may still open.
            </p>
          )}
          <Queues me={me} plan={plan} data={data} open={onOpenDashboard} />
          <Headlines me={me} data={data} open={onOpenDashboard} />
        </>
      )}

      <WhereYouCanGo me={me} openDashboard={onOpenDashboard} openSection={onOpenSection} />
    </>
  )
}
