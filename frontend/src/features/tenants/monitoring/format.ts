// Ported from services/gateway/app/static/app.js's inr()/fmtMetric() - same rounding
// and unit conventions, so a figure reads identically in both consoles during the
// migration.
import type { MetricOut } from "@/api/dashboards"

export function formatInr(paise: number): string {
  const r = paise / 100
  if (Math.abs(r) >= 1e7) return "₹" + (r / 1e7).toFixed(2) + " Cr"
  if (Math.abs(r) >= 1e5) return "₹" + (r / 1e5).toFixed(2) + " L"
  return "₹" + r.toLocaleString("en-IN", { maximumFractionDigits: 0 })
}

export function formatMetric(m: MetricOut): string {
  if (m.unit === "inr_paise") return formatInr(m.value)
  if (m.unit === "ratio") return (m.value * 100).toFixed(1) + "%"
  // "number" is a raw magnitude (a rule score, alerts-per-case) - not a percentage.
  if (m.unit === "number") return Number(m.value).toFixed(1)
  if (m.unit === "seconds") {
    const s = m.value
    if (s >= 3600) return (s / 3600).toFixed(1) + " h"
    if (s >= 60) return (s / 60).toFixed(0) + " m"
    return s.toFixed(0) + " s"
  }
  return Number(m.value).toLocaleString("en-IN")
}

// Metrics where a non-zero value is a problem, not an achievement - same list as the
// vanilla console's BAD_IF_NONZERO.
export const BAD_IF_NONZERO = new Set(["fmr_overdue_count", "str_overdue_count", "nj_breach_count"])
