import { Alert, AlertDescription } from "@/components/ui/alert"
import { BreakdownBars } from "./BreakdownBars"
import { FilterBar } from "./FilterBar"
import { formatInr } from "./format"
import { KpiCard } from "./KpiCard"
import { SavedViewsBar } from "./SavedViewsBar"
import { TransactionRowsTable } from "./TransactionRowsTable"
import { useDashboard, useDateSeverityFilters } from "./useDashboard"
import { useEvidenceAndNetwork } from "./useEvidenceAndNetwork"

export function RealtimeDashboard({ tenantId }: { tenantId: string }) {
  const { preset, setPreset, severities, setSeverities, filters, queryString, applyQuery, isCustomRange } =
    useDateSeverityFilters()
  const { dashboard, error, reload } = useDashboard(tenantId, "realtime", filters)
  const { openEvidence, dialogs } = useEvidenceAndNetwork(tenantId)

  return (
    <div className="space-y-5">
      <FilterBar
        preset={preset} setPreset={setPreset} severities={severities} setSeverities={setSeverities}
        onRefresh={reload} isCustomRange={isCustomRange}
      />
      <SavedViewsBar tenantId={tenantId} dashboardKey="realtime" currentQuery={queryString} onApply={applyQuery} />

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
            <BreakdownBars title="By rail (count)" dimension="rail" rows={dashboard.breakdowns.by_rail ?? []} />
            <BreakdownBars
              title="Value by rail"
              dimension="rail"
              rows={dashboard.breakdowns.value_by_rail ?? []}
              format={formatInr}
            />
            <BreakdownBars title="By status" dimension="status" rows={dashboard.breakdowns.by_status ?? []} />
            <BreakdownBars title="By region" dimension="region" rows={dashboard.breakdowns.by_region ?? []} />
            <BreakdownBars title="By product" dimension="product" rows={dashboard.breakdowns.by_product ?? []} />
          </div>

          <TransactionRowsTable
            onRowClick={(id) => openEvidence("transaction", id)}
            tenantId={tenantId}
            exportQuery={queryString}
          />
        </>
      )}
      {dialogs}
    </div>
  )
}
