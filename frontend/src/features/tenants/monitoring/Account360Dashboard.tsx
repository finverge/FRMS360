import { useMemo, useState } from "react"

import { Alert, AlertDescription } from "@/components/ui/alert"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { BreakdownBars } from "./BreakdownBars"
import { FilterBar } from "./FilterBar"
import { KpiCard } from "./KpiCard"
import { SavedViewsBar } from "./SavedViewsBar"
import { TransactionRowsTable } from "./TransactionRowsTable"
import { useDashboard, useDateSeverityFilters } from "./useDashboard"
import { useEvidenceAndNetwork } from "./useEvidenceAndNetwork"

// Unlike every other dashboard so far, this one is meant to be pinned to one account -
// see routes/dashboards.py's account360_dashboard docstring: "without an account it
// shows the population so an investigator can pick one from the rows".
export function Account360Dashboard({ tenantId }: { tenantId: string }) {
  const {
    preset, setPreset, severities, setSeverities, filters: baseFilters,
    queryString: baseQueryString, applyQuery: applyBaseQuery, isCustomRange,
  } = useDateSeverityFilters()
  const [accountInput, setAccountInput] = useState("")
  const [account, setAccount] = useState("")
  const { dashboard, error, reload } = useDashboard(tenantId, "account360", { ...baseFilters, account })
  const { openEvidence, openNetwork, dialogs } = useEvidenceAndNetwork(tenantId)

  // A saved view here also has to remember which account was pinned, unlike every
  // other dashboard's query - folded onto the shared date/severity query string rather
  // than growing useDateSeverityFilters an `account` field only this one persona uses.
  const queryString = useMemo(() => {
    if (!account) return baseQueryString
    const p = new URLSearchParams(baseQueryString)
    p.set("account", account)
    return p.toString()
  }, [baseQueryString, account])

  function applyQuery(qs: string) {
    applyBaseQuery(qs)
    const acct = new URLSearchParams(qs).get("account") ?? ""
    setAccount(acct)
    setAccountInput(acct)
  }

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-center gap-2">
        <Input
          placeholder="Account number (e.g. AC1234...) — leave blank for the population"
          value={accountInput}
          onChange={(e) => setAccountInput(e.target.value)}
          className="max-w-sm"
        />
        <Button size="sm" variant="outline" onClick={() => setAccount(accountInput.trim())}>Pin account</Button>
        {account && (
          <>
            <Button size="sm" variant="ghost" onClick={() => { setAccount(""); setAccountInput("") }}>
              Clear
            </Button>
            <Button size="sm" variant="outline" onClick={() => openNetwork({ account })}>
              View network
            </Button>
          </>
        )}
      </div>

      <FilterBar
        preset={preset} setPreset={setPreset} severities={severities} setSeverities={setSeverities}
        onRefresh={reload} isCustomRange={isCustomRange}
      />
      <SavedViewsBar tenantId={tenantId} dashboardKey="account360" currentQuery={queryString} onApply={applyQuery} />

      {error && (
        <Alert variant="destructive">
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      )}

      {!dashboard && !error && <p className="text-sm text-muted-foreground">Loading…</p>}

      {dashboard && (
        <>
          {account && (
            <p className="text-sm text-muted-foreground">
              Pinned to <span className="font-mono">{account}</span>.
            </p>
          )}

          <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
            {Object.values(dashboard.metrics).map((m) => (
              <KpiCard key={m.name} metric={m} />
            ))}
          </div>

          <div className="grid gap-4 sm:grid-cols-2">
            <BreakdownBars title="By rail" dimension="rail" rows={dashboard.breakdowns.by_rail ?? []} />
            <BreakdownBars title="Alerts by family" dimension="family" rows={dashboard.breakdowns.by_family ?? []} />
            <BreakdownBars title="By status" dimension="status" rows={dashboard.breakdowns.by_status ?? []} />
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
