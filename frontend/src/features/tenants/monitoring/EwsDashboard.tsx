import { Alert, AlertDescription } from "@/components/ui/alert"
import { AlertRowsTable } from "./AlertRowsTable"
import { BreakdownBars } from "./BreakdownBars"
import { DormantPanel } from "./DormantPanel"
import { FilterBar } from "./FilterBar"
import { KpiCard } from "./KpiCard"
import { SavedViewsBar } from "./SavedViewsBar"
import { useDashboard, useDateSeverityFilters } from "./useDashboard"
import { useEvidenceAndNetwork } from "./useEvidenceAndNetwork"

export function EwsDashboard({ tenantId }: { tenantId: string }) {
  const { preset, setPreset, severities, setSeverities, filters, queryString, applyQuery, isCustomRange } =
    useDateSeverityFilters()
  const { dashboard, error, reload } = useDashboard(tenantId, "ews", filters)
  const { openEvidence, dialogs } = useEvidenceAndNetwork(tenantId)

  return (
    <div className="space-y-5">
      <FilterBar
        preset={preset} setPreset={setPreset} severities={severities} setSeverities={setSeverities}
        onRefresh={reload} isCustomRange={isCustomRange}
      />
      <SavedViewsBar tenantId={tenantId} dashboardKey="ews" currentQuery={queryString} onApply={applyQuery} />

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

          {dashboard.dormant && <DormantPanel dormant={dashboard.dormant} />}

          <div className="grid gap-4 sm:grid-cols-2">
            <BreakdownBars title="By family" dimension="family" rows={dashboard.breakdowns.by_family ?? []} />
            <BreakdownBars title="By severity" dimension="severity" rows={dashboard.breakdowns.by_severity ?? []} />
            <BreakdownBars title="By rule" dimension="rule" rows={dashboard.breakdowns.by_rule ?? []} />
            <BreakdownBars title="By typology" dimension="typology" rows={dashboard.breakdowns.by_typology ?? []} />
            <BreakdownBars
              title="Precision by family"
              dimension="family"
              rows={dashboard.breakdowns.precision_by_family ?? []}
              format={(v) => (v * 100).toFixed(1) + "%"}
            />
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
