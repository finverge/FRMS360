import type { DormantReport } from "@/api/dashboards"
import { Badge } from "@/components/ui/badge"

// Ported from the vanilla console's renderDormant() - which configured (quantitative)
// EWS indicators produced nothing in this window, and why, so silence is visible
// instead of just absent from every alert chart. See rules.py's dormant_report().
export function DormantPanel({ dormant }: { dormant: DormantReport }) {
  if (!dormant.available) {
    return (
      <div className="space-y-2 rounded-md border border-border p-4">
        <h5 className="text-sm font-medium">Indicator coverage</h5>
        <p className="text-sm text-muted-foreground">
          The rule catalogue could not be read, so coverage is unknown. Reporting every rule as dormant here
          would be a false alarm.
        </p>
      </div>
    )
  }

  const pct = Math.round((dormant.coverage ?? 0) * 100)
  const good = pct >= 80

  return (
    <div className="space-y-3 rounded-md border border-border p-4">
      <div className="flex items-center justify-between">
        <h5 className="text-sm font-medium">Indicator coverage</h5>
        <Badge variant={good ? "success" : "destructive"}>{pct}% firing</Badge>
      </div>
      <div className="h-2 overflow-hidden rounded bg-muted">
        <div className={"h-full " + (good ? "bg-success" : "bg-destructive")} style={{ width: `${pct}%` }} />
      </div>
      <p className="text-xs text-muted-foreground">
        {dormant.fired_quantitative} of {dormant.configured_quantitative} quantitative indicators fired in this
        window · {dormant.configured} configured in total
      </p>

      {dormant.dormant.length > 0 ? (
        <div className="overflow-x-auto rounded-md border border-border">
          <table className="w-full text-sm">
            <thead className="bg-muted text-left text-muted-foreground">
              <tr>
                <th className="px-3 py-2 font-medium">Rule</th>
                <th className="px-3 py-2 font-medium">Family</th>
                <th className="px-3 py-2 font-medium">Would fire when</th>
                <th className="px-3 py-2 font-medium">Threshold</th>
              </tr>
            </thead>
            <tbody>
              {dormant.dormant.map((r) => (
                <tr key={r.rule_id} className="border-t border-border">
                  <td className="px-3 py-2 font-mono text-xs">{r.rule_id}</td>
                  <td className="px-3 py-2"><Badge variant="outline">{r.family}</Badge></td>
                  <td className="px-3 py-2">{r.reason}</td>
                  <td className="px-3 py-2 font-mono text-xs">
                    {r.threshold ?? "—"} {r.unit || ""}
                    {r.blocked_by && <div className="text-muted-foreground">blocked: {r.blocked_by}</div>}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <p className="text-sm text-success">Every configured quantitative indicator produced at least one signal in this window.</p>
      )}

      {dormant.dormant_qualitative.length > 0 && (
        <p className="text-xs text-muted-foreground">
          Qualitative indicators silent: {dormant.dormant_qualitative.join(", ")} — expected, these are fed
          from CBS events and relationship-manager input rather than the payment stream.
        </p>
      )}

      {dormant.uncatalogued.length > 0 && (
        <p className="text-xs text-destructive">
          Firing but not in the catalogue: {dormant.uncatalogued.join(", ")} — detection and configuration
          have drifted apart.
        </p>
      )}
    </div>
  )
}
