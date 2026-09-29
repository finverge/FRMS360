import type { MetricOut } from "@/api/dashboards"
import { BAD_IF_NONZERO, formatMetric } from "./format"

export function KpiCard({ metric }: { metric: MetricOut }) {
  let tone = ""
  if (BAD_IF_NONZERO.has(metric.name)) tone = metric.value > 0 ? "text-destructive" : "text-success"
  else if (metric.name === "precision") tone = metric.value >= 0.3 ? "text-success" : "text-destructive"

  return (
    <div className="rounded-lg border border-border bg-card p-4 shadow-sm">
      <div className="text-xs font-medium tracking-wide text-muted-foreground uppercase">{metric.label}</div>
      <div className={"text-2xl font-semibold " + tone}>{formatMetric(metric)}</div>
      <div className="text-xs text-muted-foreground">
        {metric.unit === "inr_paise" ? `exact: ${metric.value.toLocaleString()} paise` : metric.name}
      </div>
    </div>
  )
}
