import type { ReconciliationOut } from "@/api/dashboards"
import { Badge } from "@/components/ui/badge"

// Presented next to the figures it backs, not as a separate audit screen - the point
// (per reconciliation.py's module docstring) is showing a number together with the
// proof that it ties, so a supervisor never has to take "the total is correct" on
// faith. Every check is a live invariant re-run against the current filter set, not a
// cached report.
export function ReconciliationPanel({ reconciliation }: { reconciliation: ReconciliationOut }) {
  const { summary, checks, status, checked_at } = reconciliation
  const failed = checks.filter((c) => !c.passed)

  return (
    <div className="space-y-3 rounded-md border border-border p-4">
      <div className="flex items-center justify-between">
        <h5 className="text-sm font-medium">Reconciliation</h5>
        <Badge variant={status === "pass" ? "success" : "destructive"}>
          {status === "pass" ? "all checks pass" : `${summary.failed} check(s) failing`}
        </Badge>
      </div>
      <p className="text-xs text-muted-foreground">
        {summary.passed} of {summary.total} invariants hold
        {summary.critical_failures > 0 && `, ${summary.critical_failures} critical failure(s)`}
        {checked_at && ` · checked ${new Date(checked_at).toLocaleString("en-IN")}`}
      </p>

      {failed.length > 0 && (
        <div className="overflow-x-auto rounded-md border border-border">
          <table className="w-full text-sm">
            <thead className="bg-muted text-left text-muted-foreground">
              <tr>
                <th className="px-3 py-2 font-medium">Check</th>
                <th className="px-3 py-2 font-medium">Severity</th>
                <th className="px-3 py-2 text-right font-medium">Expected</th>
                <th className="px-3 py-2 text-right font-medium">Actual</th>
                <th className="px-3 py-2 font-medium">Detail</th>
              </tr>
            </thead>
            <tbody>
              {failed.map((c) => (
                <tr key={c.id} className="border-t border-border">
                  <td className="px-3 py-2">
                    <span className="font-mono text-xs">{c.id}</span> {c.title}
                  </td>
                  <td className="px-3 py-2 capitalize">{c.severity}</td>
                  <td className="px-3 py-2 text-right tabular-nums">{c.expected.toLocaleString("en-IN")}</td>
                  <td className="px-3 py-2 text-right tabular-nums text-destructive">{c.actual.toLocaleString("en-IN")}</td>
                  <td className="px-3 py-2 text-muted-foreground">{c.detail}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}
