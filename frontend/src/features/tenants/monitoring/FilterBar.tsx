import { Button } from "@/components/ui/button"
import { Badge } from "@/components/ui/badge"
import { DATE_PRESETS, SEVERITIES } from "./useDashboard"

export function FilterBar({
  preset,
  setPreset,
  severities,
  setSeverities,
  onRefresh,
  isCustomRange,
}: {
  preset: string
  setPreset: (p: string) => void
  severities: string[]
  setSeverities: (s: string[]) => void
  onRefresh: () => void
  // True while a saved view's exact date range is applied, overriding every preset -
  // none of the preset buttons is "active" in that state, which would otherwise read
  // as a lie about what window is actually loaded.
  isCustomRange?: boolean
}) {
  // Each severity gets its own token color so the filter chips double as a legend for
  // the severity coloring used elsewhere (KPI tones, badges) - not just an arbitrary
  // on/off control.
  const SEVERITY_STYLE: Record<string, string> = {
    critical: "border-severity-critical/40 text-severity-critical data-[on=true]:bg-severity-critical",
    high: "border-severity-high/40 text-severity-high data-[on=true]:bg-severity-high",
    medium: "border-severity-medium/40 text-severity-medium data-[on=true]:bg-severity-medium",
    low: "border-severity-low/40 text-severity-low data-[on=true]:bg-severity-low",
  }

  return (
    <div className="flex flex-wrap items-center gap-4">
      <div className="inline-flex h-9 w-fit items-center rounded-lg border border-border bg-muted p-1 text-sm text-muted-foreground">
        {DATE_PRESETS.map((p) => (
          <button
            key={p.key}
            type="button"
            onClick={() => setPreset(p.key)}
            className={
              "h-7 rounded-md px-3 font-medium transition-colors " +
              (!isCustomRange && preset === p.key
                ? "bg-primary text-primary-foreground shadow-sm"
                : "hover:text-foreground")
            }
          >
            {p.label}
          </button>
        ))}
      </div>
      {isCustomRange && <Badge variant="outline">custom range from saved view</Badge>}
      <div className="flex flex-wrap items-center gap-1.5">
        {SEVERITIES.map((s) => {
          const on = severities.includes(s)
          return (
            <button
              key={s}
              type="button"
              data-on={on}
              onClick={() =>
                setSeverities(on ? severities.filter((x) => x !== s) : [...severities, s])
              }
              className={
                "rounded-full border px-2.5 py-1 text-xs font-medium capitalize transition-colors " +
                "focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none " +
                "data-[on=true]:text-white " +
                SEVERITY_STYLE[s]
              }
            >
              {s}
            </button>
          )
        })}
      </div>
      <Button size="sm" variant="ghost" onClick={onRefresh}>Refresh</Button>
    </div>
  )
}
