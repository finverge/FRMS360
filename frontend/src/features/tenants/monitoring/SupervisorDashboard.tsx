import { Alert, AlertDescription } from "@/components/ui/alert"
import { BreakdownBars } from "./BreakdownBars"
import { CaseRowsTable } from "./CaseRowsTable"
import { FilterBar } from "./FilterBar"
import { formatInr } from "./format"
import { KpiCard } from "./KpiCard"
import { ReconciliationPanel } from "./ReconciliationPanel"
import { SavedViewsBar } from "./SavedViewsBar"
import { useDashboard, useDateSeverityFilters } from "./useDashboard"
import { useEvidenceAndNetwork } from "./useEvidenceAndNetwork"

export function SupervisorDashboard({ tenantId }: { tenantId: string }) {
  const { preset, setPreset, severities, setSeverities, filters, queryString, applyQuery, isCustomRange } =
    useDateSeverityFilters()
  const { dashboard, error, reload } = useDashboard(tenantId, "supervisor", filters)
  const { openEvidence, dialogs } = useEvidenceAndNetwork(tenantId)

  return (
    <div className="space-y-5">
      <FilterBar
        preset={preset} setPreset={setPreset} severities={severities} setSeverities={setSeverities}
        onRefresh={reload} isCustomRange={isCustomRange}
      />
      <SavedViewsBar tenantId={tenantId} dashboardKey="supervisor" currentQuery={queryString} onApply={applyQuery} />

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

          {dashboard.reconciliation && <ReconciliationPanel reconciliation={dashboard.reconciliation} />}

          <div className="grid gap-4 sm:grid-cols-2">
            <BreakdownBars title="Cases by state" dimension="state" rows={dashboard.breakdowns.cases_by_state ?? []} />
            <BreakdownBars
              title="Fraud value by FMR category"
              dimension="fmr_category"
              rows={dashboard.breakdowns.by_fmr_category ?? []}
              format={formatInr}
            />
          </div>

          <CaseRowsTable
            onRowClick={(id) => openEvidence("case", id)}
            tenantId={tenantId}
            exportQuery={queryString}
          />
        </>
      )}
      {dialogs}
    </div>
  )
}
