import { useEffect, useState } from "react"

import { useSession } from "@/app/SessionContext"
import { ApiError } from "@/api/client"
import { getMuleRiskIndicators, type MuleRiskIndicatorsOut } from "@/api/evidence"
import { Alert, AlertDescription } from "@/components/ui/alert"
import { Card, CardContent } from "@/components/ui/card"
import { StatCell } from "@/components/ui/sidebar"
import { formatInr } from "./format"

const WINDOWS: { value: "1d" | "1w" | "1m"; label: string }[] = [
  { value: "1d", label: "1D" },
  { value: "1w", label: "1W" },
  { value: "1m", label: "1M" },
]

// One account's behavioural/network/velocity signals - the same "sender or receiver"
// framing Verafye's own Mule Risk Indicators panel uses, since a transaction has two
// parties and each can be inspected separately. Every number here is a real aggregate
// over fact_transaction (see postgres.py's mule_risk_indicators()) - there is no
// sanctions/adverse-media score in this product, so this never claims to be one.
export function MuleRiskPanel({
  tenantId,
  debtorAccount,
  creditorAccount,
  revealed,
}: {
  tenantId: string
  debtorAccount?: string
  creditorAccount?: string
  revealed: boolean
}) {
  const { accessToken } = useSession()
  const [party, setParty] = useState<"debtor" | "creditor">("debtor")
  const [window, setWindow] = useState<"1d" | "1w" | "1m">("1m")
  const [data, setData] = useState<MuleRiskIndicatorsOut | null>(null)
  const [error, setError] = useState<string | null>(null)

  const account = party === "debtor" ? debtorAccount : creditorAccount

  useEffect(() => {
    if (!revealed || !account) return
    setData(null)
    setError(null)
    getMuleRiskIndicators(tenantId, account, accessToken)
      .then(setData)
      .catch((err) => setError(err instanceof ApiError ? err.message : "Could not load risk indicators."))
  }, [tenantId, account, revealed, accessToken])

  if (!revealed) {
    return (
      <p className="text-sm text-muted-foreground">
        Reveal PII (in the panel on the right) to inspect this account's network and velocity signals -
        these figures are computed from real account numbers, so a masked value cannot be queried.
      </p>
    )
  }

  if (!account) {
    return <p className="text-sm text-muted-foreground">No account on this side of the transaction.</p>
  }

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="inline-flex h-8 w-fit items-center rounded-lg bg-muted p-1 text-sm text-muted-foreground">
          {(["debtor", "creditor"] as const).map((p) => (
            <button
              key={p}
              type="button"
              disabled={p === "debtor" ? !debtorAccount : !creditorAccount}
              onClick={() => setParty(p)}
              className={
                "h-6 rounded-md px-3 font-medium whitespace-nowrap transition-colors disabled:opacity-40 " +
                (party === p ? "bg-background text-foreground shadow-sm" : "")
              }
            >
              {p === "debtor" ? "Sender" : "Receiver"}
            </button>
          ))}
        </div>
        <div className="inline-flex h-8 w-fit items-center rounded-lg bg-muted p-1 text-sm text-muted-foreground">
          {WINDOWS.map((w) => (
            <button
              key={w.value}
              type="button"
              onClick={() => setWindow(w.value)}
              className={
                "h-6 rounded-md px-3 font-medium transition-colors " +
                (window === w.value ? "bg-background text-foreground shadow-sm" : "")
              }
            >
              {w.label}
            </button>
          ))}
        </div>
      </div>

      {error && (
        <Alert variant="destructive">
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      )}

      {!data && !error && <p className="text-sm text-muted-foreground">Loading…</p>}

      {data && (
        <>
          <div className="space-y-2">
            <h6 className="text-xs font-medium tracking-wide text-muted-foreground uppercase">Network linkage</h6>
            <div className="grid grid-cols-2 gap-3 sm:grid-cols-5">
              <Card className="py-3"><CardContent className="px-4"><StatCell label="Linked devices" value={data.network_linkage.linked_devices} /></CardContent></Card>
              <Card className="py-3"><CardContent className="px-4"><StatCell label="Linked accounts" value={data.network_linkage.linked_accounts} /></CardContent></Card>
              <Card className="py-3"><CardContent className="px-4"><StatCell label="Linked IPs" value={data.network_linkage.linked_ips} /></CardContent></Card>
              <Card className="py-3"><CardContent className="px-4"><StatCell label="Branches" value={data.network_linkage.distinct_branches} /></CardContent></Card>
              <Card className="py-3"><CardContent className="px-4"><StatCell label="Regions" value={data.network_linkage.distinct_regions} /></CardContent></Card>
            </div>
          </div>

          <div className="space-y-2">
            <h6 className="text-xs font-medium tracking-wide text-muted-foreground uppercase">Transaction flow</h6>
            <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
              <Card className="py-3"><CardContent className="px-4"><StatCell label="Funds-in counterparties" value={data.transaction_flow.funds_in_counterparties} /></CardContent></Card>
              <Card className="py-3"><CardContent className="px-4"><StatCell label="Funds-out counterparties" value={data.transaction_flow.funds_out_counterparties} /></CardContent></Card>
              <Card className="py-3"><CardContent className="px-4"><StatCell label={`Funds-in txns (${window.toUpperCase()})`} value={data.transaction_flow.funds_in_txn_count[window]} /></CardContent></Card>
              <Card className="py-3"><CardContent className="px-4"><StatCell label={`Funds-out txns (${window.toUpperCase()})`} value={data.transaction_flow.funds_out_txn_count[window]} /></CardContent></Card>
            </div>
          </div>

          <div className="space-y-2">
            <h6 className="text-xs font-medium tracking-wide text-muted-foreground uppercase">Velocity & movement</h6>
            <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
              <Card className="py-3"><CardContent className="px-4"><StatCell label="Funds-in" value={formatInr(data.velocity.funds_in_paise)} /></CardContent></Card>
              <Card className="py-3"><CardContent className="px-4"><StatCell label="Funds-out" value={formatInr(data.velocity.funds_out_paise)} /></CardContent></Card>
              <Card className="py-3"><CardContent className="px-4"><StatCell label="Net flow" value={formatInr(data.velocity.net_flow_paise)} tone={data.velocity.net_flow_paise < 0 ? "warning" : "default"} /></CardContent></Card>
              <Card className="py-3"><CardContent className="px-4"><StatCell label="Max txn" value={formatInr(data.transaction_flow.max_txn_paise)} /></CardContent></Card>
            </div>
          </div>

          <p className="text-xs text-muted-foreground">
            {data.total_txn_count} total transaction{data.total_txn_count === 1 ? "" : "s"} across this account's history -
            ledger signals only. To check a name against the OFAC sanctions list, use Sanctions Screening
            in the sidebar (this product has no customer name on file for this account to screen automatically).
          </p>
        </>
      )}
    </div>
  )
}
