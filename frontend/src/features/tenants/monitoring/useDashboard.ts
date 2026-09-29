import { useEffect, useMemo, useState } from "react"

import { useSession } from "@/app/SessionContext"
import { ApiError } from "@/api/client"
import { getDashboard, type DashboardFilters, type DashboardOut } from "@/api/dashboards"

export const SEVERITIES = ["critical", "high", "medium", "low"]

export function isoDaysAgo(days: number): string {
  return new Date(Date.now() - days * 86400000).toISOString()
}

export const DATE_PRESETS: { key: string; label: string; days: number }[] = [
  { key: "7d", label: "Last 7 days", days: 7 },
  { key: "30d", label: "Last 30 days", days: 30 },
  { key: "90d", label: "Last 90 days", days: 90 },
]

/** Shared date-preset + severity filter state, used by every persona dashboard so far -
 * the rest of the ~20-field filter set is not wired to any of them yet, so that is also
 * as much of a saved view as one can capture right now. A saved view (or a preset
 * click) can push an explicit date_from/date_to that overrides the preset's own
 * "now minus N days" computation - see applyQuery(). */
export function useDateSeverityFilters() {
  const [preset, setPreset] = useState("30d")
  const [severities, setSeverities] = useState<string[]>([])
  const [customRange, setCustomRange] = useState<{ date_from: string; date_to: string } | null>(null)
  const severityKey = severities.join(",")

  // Memoized on the *selection*, not recomputed every render: `date_to: new
  // Date().toISOString()` used to run fresh (down to the millisecond) on every render,
  // so useDashboard's effect - keyed on this object's fields - never saw a stable
  // dependency and refired every render. That's a tight render->fetch->setState->
  // render loop, invisible until it hits the gateway's rate limiter.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  const filters: DashboardFilters = useMemo(() => {
    if (customRange) return { ...customRange, severities }
    const days = DATE_PRESETS.find((p) => p.key === preset)?.days ?? 30
    return { date_from: isoDaysAgo(days), date_to: new Date().toISOString(), severities }
  }, [preset, severityKey, customRange, severities])

  // The query string a saved view stores and restores - the same shape
  // api/dashboards.ts's buildQuery() sends, minus the leading "?".
  const queryString = useMemo(() => {
    const p = new URLSearchParams()
    if (filters.date_from) p.set("date_from", filters.date_from)
    if (filters.date_to) p.set("date_to", filters.date_to)
    for (const s of severities) p.append("severities", s)
    return p.toString()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [filters.date_from, filters.date_to, severityKey])

  function setPresetAndClearCustom(p: string) {
    setCustomRange(null)
    setPreset(p)
  }

  function applyQuery(qs: string) {
    const p = new URLSearchParams(qs)
    const df = p.get("date_from")
    const dt = p.get("date_to")
    setCustomRange(df && dt ? { date_from: df, date_to: dt } : null)
    setSeverities(p.getAll("severities"))
  }

  return {
    preset,
    setPreset: setPresetAndClearCustom,
    severities,
    setSeverities,
    filters,
    queryString,
    applyQuery,
    isCustomRange: !!customRange,
  }
}

/** Fetches one persona dashboard and re-fetches whenever the filters change. Every
 * dashboard route shares the same query shape and DashboardOut envelope (see
 * routes/dashboards.py) - only the fields inside differ per persona. */
export function useDashboard(tenantId: string, dashboardKey: string, filters: DashboardFilters) {
  const { accessToken } = useSession()
  const [dashboard, setDashboard] = useState<DashboardOut | null>(null)
  const [error, setError] = useState<string | null>(null)

  function reload() {
    getDashboard(tenantId, dashboardKey, filters, accessToken)
      .then((d) => {
        setDashboard(d)
        setError(null)
      })
      .catch((err) => setError(err instanceof ApiError ? err.message : "Could not load this dashboard."))
  }

  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(reload, [
    tenantId, dashboardKey, accessToken,
    filters.date_from, filters.date_to, (filters.severities ?? []).join(","), filters.account ?? "",
  ])

  return { dashboard, error, reload }
}
