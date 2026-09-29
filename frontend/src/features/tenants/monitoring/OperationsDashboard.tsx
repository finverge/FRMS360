import { Alert, AlertDescription } from "@/components/ui/alert"
import { AlertRowsTable } from "./AlertRowsTable"
import { BreakdownBars } from "./BreakdownBars"
import { FilterBar } from "./FilterBar"
import { KpiCard } from "./KpiCard"
import { SavedViewsBar } from "./SavedViewsBar"
import { useDashboard, useDateSeverityFilters } from "./useDashboard"
import { useEvidenceAndNetwork } from "./useEvidenceAndNetwork"

// Backend route/dashboard key is "risk_manager" (Fraud Risk Manager persona), but the
// platform catalogue (cp_common.rbac.DASHBOARD_META) labels this dashboard
// "Operations" - the console follows the catalogue's label, matching every other tab.
export function OperationsDashboard({ tenantId }: { tenantId: string }) {
  const { preset, setPreset, severities, setSeverities, filters, queryString, applyQuery, isCustomRange } =
    useDateSeverityFilters()
  const { dashboard, error, reload } = useDashboard(tenantId, "risk_manager", filters)
  const { openEvidence, dialogs } = useEvidenceAndNetwork(tenantId)

  return (
    <div className="space-y-5">
      <FilterBar
        preset={preset} setPreset={setPreset} severities={severities} setSeverities={setSeverities}
        onRefresh={reload} isCustomRange={isCustomRange}
      />
      <SavedViewsBar tenantId={tenantId} dashboardKey="risk_manager" currentQuery={queryString} onApply={applyQuery} />

      {error && (
        <Alert variant="destructive">
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      )}

      {!dashboard && !error && <p className="text-sm text-muted-foreground">Loading…</p>}

      {dashboard && (
        <>
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
            {Object.values(dashboard.metrics).map((m) => (
              <KpiCard key={m.name} metric={m} />
            ))}
          </div>

          <div className="grid gap-4 sm:grid-cols-2">
            <BreakdownBars title="By analyst" dimension="analyst" rows={dashboard.breakdowns.by_analyst ?? []} />
            <BreakdownBars title="By severity" dimension="severity" rows={dashboard.breakdowns.by_severity ?? []} />
            <BreakdownBars title="By disposition" dimension="disposition" rows={dashboard.breakdowns.by_disposition ?? []} />
            <BreakdownBars title="By rail" dimension="rail" rows={dashboard.breakdowns.by_rail ?? []} />
            <BreakdownBars title="Cases by assignee" dimension="assignee" rows={dashboard.breakdowns.cases_by_assignee ?? []} />
          </div>

          <AlertRowsTable
            onRowClick={(id) => openEvidence("alert", id)}
            tenantId={tenantId}
            exportQuery={queryString}
          />
        </>
      )}
      {dialogs}
    </div>
  )
}
