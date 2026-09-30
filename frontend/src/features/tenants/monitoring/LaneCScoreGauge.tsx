import type { LaneCScoreOut } from "@/api/lanec"

// A 0-100 credit health score has no existing visual component in this console (every
// other "score" here is a plain table cell) - this is the one new display primitive
// Lane C needs. Bands match credit_health_scorer.py's own _recommendation() thresholds
// exactly (>=80 continue, >=60 monitor, >=40 investigate, else escalate), so the color
// a credit analyst sees here can never disagree with the recommendation string next to it.
function bandTone(score: number): { bar: string; text: string } {
  if (score >= 80) return { bar: "bg-success", text: "text-success" }
  if (score >= 60) return { bar: "bg-warning", text: "text-warning" }
  if (score >= 40) return { bar: "bg-severity-high", text: "text-severity-high" }
  return { bar: "bg-destructive", text: "text-destructive" }
}

const TREND_LABEL: Record<string, string> = {
  improving: "↑ Improving",
  stable: "→ Stable",
  deteriorating: "↓ Deteriorating",
  new: "First period on record",
}

export function LaneCScoreGauge({ score }: { score: LaneCScoreOut }) {
  if (!score.available || score.score_value == null) {
    return (
      <div className="rounded-lg border border-border bg-card p-4 text-sm text-muted-foreground">
        No scored statement on record yet for this borrower.
      </div>
    )
  }

  const tone = bandTone(score.score_value)

  return (
    <div className="rounded-lg border border-border bg-card p-4 shadow-sm">
      <div className="flex items-baseline justify-between">
        <div className="text-xs font-medium tracking-wide text-muted-foreground uppercase">
          Credit Health Score
        </div>
        <span className="text-xs text-muted-foreground">
          as of {score.reporting_date ? new Date(score.reporting_date).toLocaleDateString("en-IN") : "—"}
        </span>
      </div>

      <div className="mt-1 flex items-end gap-2">
        <span className={"text-3xl font-semibold " + tone.text}>{score.score_value}</span>
        <span className="pb-1 text-sm text-muted-foreground">/ 100</span>
      </div>

      <div className="mt-2 h-2 w-full overflow-hidden rounded-full bg-muted">
        <div
          className={"h-full rounded-full " + tone.bar}
          style={{ width: `${score.score_value}%` }}
        />
      </div>

      <div className="mt-3 flex flex-wrap items-center justify-between gap-2 text-sm">
        <span className="text-muted-foreground">{TREND_LABEL[score.trend ?? "new"]}</span>
        <span className={"font-medium capitalize " + tone.text}>{score.recommendation}</span>
      </div>

      {(score.signal_count ?? 0) > 0 && (
        <div className="mt-2 text-xs text-muted-foreground">
          {score.signal_count} signal{score.signal_count === 1 ? "" : "s"} fired
          {(score.critical_count ?? 0) > 0 &&
            ` (${score.critical_count} critical)`}
        </div>
      )}
    </div>
  )
}
