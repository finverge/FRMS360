// A dependency-free stand-in for the vanilla console's ECharts bars (app.js's
// palette()/renderCharts()). Charting a real library (ECharts, recharts) in is real,
// separately-scoped work - this renders the same rows honestly, just as bars instead
// of a canvas chart, so the dashboard isn't blocked on that decision.
// References the same --severity-*/--success/--destructive/--warning tokens used by
// the FilterBar chips and the row-table badges (SeverityBadge/DispositionBadge), so a
// severity or disposition is the same color everywhere in the console, light or dark.
const SEV_COLOR: Record<string, string> = {
  critical: "var(--color-severity-critical)",
  high: "var(--color-severity-high)",
  medium: "var(--color-severity-medium)",
  low: "var(--color-severity-low)",
}
const DISP_COLOR: Record<string, string> = {
  true_positive: "var(--color-success)",
  false_positive: "var(--color-muted-foreground)",
  pending: "var(--color-warning)",
}
const PALETTE = ["#0F6E7A", "#19A6B8", "#6B5B95", "#C77F17", "#2E8B6F", "#C4453B", "#8A93A1", "#4E7CA1"]

function colorFor(dimension: string, key: string, index: number): string {
  if (dimension === "severity" && SEV_COLOR[key]) return SEV_COLOR[key]
  if (dimension === "disposition" && DISP_COLOR[key]) return DISP_COLOR[key]
  return PALETTE[index % PALETTE.length]
}

export function BreakdownBars({
  title,
  dimension,
  rows,
  // Most breakdowns are counts, sized proportionally against the largest count in the
  // set. A ratio-valued breakdown (e.g. precision_by_family) still sizes bars the same
  // way, but needs its own display text - "61.3%" rather than "0.613".
  format = (v: number) => v.toLocaleString("en-IN"),
}: {
  title: string
  dimension: string
  rows: { key: string; value: number }[]
  format?: (value: number) => string
}) {
  const max = Math.max(1e-9, ...rows.map((r) => r.value))
  return (
    <div className="space-y-2 rounded-md border border-border p-4">
      <h5 className="text-sm font-medium">{title}</h5>
      {rows.length === 0 && <p className="text-sm text-muted-foreground">No data in this window.</p>}
      <div className="space-y-1.5">
        {rows.map((r, i) => (
          <div key={r.key || i} className="flex items-center gap-2 text-sm">
            <span className="w-28 shrink-0 truncate text-muted-foreground" title={r.key || "(none)"}>
              {r.key || "(none)"}
            </span>
            <div className="h-4 flex-1 overflow-hidden rounded bg-muted">
              <div
                className="h-full rounded"
                style={{ width: `${(r.value / max) * 100}%`, backgroundColor: colorFor(dimension, r.key, i) }}
              />
            </div>
            <span className="w-16 shrink-0 text-right tabular-nums">{format(r.value)}</span>
          </div>
        ))}
      </div>
    </div>
  )
}
