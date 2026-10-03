import { useEffect, useState } from "react"

import { useSession } from "@/app/SessionContext"
import { hasPermission } from "@/lib/access"
import { ApiError } from "@/api/client"
import {
  getLatestScore, getSignals, ingestStatement, listAlerts, reviewAlert,
  type LaneCAlertOut, type LaneCScoreOut, type LaneCSignalOut,
} from "@/api/lanec"
import { Alert, AlertDescription } from "@/components/ui/alert"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { PageHeader } from "@/components/ui/sidebar"
import { LaneCScoreGauge } from "./LaneCScoreGauge"
import { SeverityBadge } from "./SeverityBadge"

const FILING_TYPES = ["annual", "quarterly", "interim"] as const
const SELECT_CLASS =
  "flex h-9 w-full rounded-md border border-input bg-background px-3 py-1 text-sm shadow-sm " +
  "focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 outline-none"

function errorMessage(err: unknown, fallback: string) {
  return err instanceof ApiError ? err.message : fallback
}

// -------------------------------------------------------------- upload
function UploadStatementCard({ tenantId }: { tenantId: string }) {
  const { accessToken } = useSession()
  const [account, setAccount] = useState("")
  const [reportingDate, setReportingDate] = useState("")
  const [filingType, setFilingType] = useState<(typeof FILING_TYPES)[number]>("annual")
  const [file, setFile] = useState<File | null>(null)
  const [busy, setBusy] = useState(false)
  const [result, setResult] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  const canSubmit = account.trim() && reportingDate && file && !busy

  function handleSubmit() {
    if (!canSubmit || !file) return
    setBusy(true)
    setError(null)
    setResult(null)
    ingestStatement(tenantId, { account: account.trim(), reportingDate, filingType, file }, accessToken!)
      .then((r) => {
        setResult(
          r.resubmission
            ? `Resubmission accepted for ${account.trim()} — queued for reprocessing.`
            : `Statement accepted for ${account.trim()} — queued for processing. Run the Lane C batch worker to score it.`
        )
        setFile(null)
      })
      .catch((err) => setError(errorMessage(err, "Could not submit this statement.")))
      .finally(() => setBusy(false))
  }

  return (
    <div className="space-y-3 rounded-lg border border-border bg-card p-4 shadow-sm">
      <h6 className="text-sm font-medium">Submit a financial statement</h6>
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-4">
        <div className="sm:col-span-2">
          <label className="mb-1 block text-xs text-muted-foreground">Borrower account</label>
          <Input value={account} onChange={(e) => setAccount(e.target.value)} placeholder="AC-..." />
        </div>
        <div>
          <label className="mb-1 block text-xs text-muted-foreground">Reporting date</label>
          <Input type="date" value={reportingDate} onChange={(e) => setReportingDate(e.target.value)} />
        </div>
        <div>
          <label className="mb-1 block text-xs text-muted-foreground">Filing type</label>
          <select className={SELECT_CLASS} value={filingType}
                  onChange={(e) => setFilingType(e.target.value as typeof filingType)}>
            {FILING_TYPES.map((t) => (
              <option key={t} value={t}>{t[0].toUpperCase() + t.slice(1)}</option>
            ))}
          </select>
        </div>
      </div>
      <div className="flex flex-wrap items-center gap-2">
        <input
          type="file"
          accept="application/pdf"
          onChange={(e) => setFile(e.target.files?.[0] ?? null)}
          className="text-sm text-muted-foreground file:mr-3 file:rounded-md file:border file:border-input file:bg-background file:px-3 file:py-1 file:text-sm"
        />
        <Button onClick={handleSubmit} disabled={!canSubmit}>
          {busy ? "Submitting…" : "Submit statement"}
        </Button>
      </div>
      {error && (
        <Alert variant="destructive"><AlertDescription>{error}</AlertDescription></Alert>
      )}
      {result && <p className="text-sm text-success">{result}</p>}
    </div>
  )
}

// -------------------------------------------------------------- borrower lookup
function BorrowerLookupCard({ tenantId }: { tenantId: string }) {
  const { accessToken } = useSession()
  const [account, setAccount] = useState("")
  const [busy, setBusy] = useState(false)
  const [score, setScore] = useState<LaneCScoreOut | null>(null)
  const [signals, setSignals] = useState<LaneCSignalOut[] | null>(null)
  const [error, setError] = useState<string | null>(null)

  function handleLookup() {
    const acc = account.trim()
    if (!acc) return
    setBusy(true)
    setError(null)
    setScore(null)
    setSignals(null)
    getLatestScore(tenantId, acc, accessToken!)
      .then((s) => {
        setScore(s)
        if (s.available && s.reporting_date) {
          return getSignals(tenantId, acc, s.reporting_date, accessToken!).then((r) => setSignals(r.signals))
        }
        return undefined
      })
      .catch((err) => setError(errorMessage(err, "Could not look up this borrower.")))
      .finally(() => setBusy(false))
  }

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-2">
        <Input
          value={account}
          onChange={(e) => setAccount(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && handleLookup()}
          placeholder="Borrower account (e.g. AC-...)"
          className="max-w-sm"
        />
        <Button onClick={handleLookup} disabled={busy || !account.trim()}>
          {busy ? "Looking up…" : "Look up"}
        </Button>
      </div>

      {error && <Alert variant="destructive"><AlertDescription>{error}</AlertDescription></Alert>}

      {score && (
        <div className="grid grid-cols-1 gap-4 lg:grid-cols-[280px_1fr]">
          <LaneCScoreGauge score={score} />
          {signals && signals.length > 0 && (
            <div className="overflow-x-auto rounded-lg border border-border">
              <table className="w-full text-sm">
                <thead className="bg-muted text-left text-muted-foreground">
                  <tr>
                    <th className="px-3 py-1.5 font-medium">Signal</th>
                    <th className="px-3 py-1.5 font-medium">Status</th>
                    <th className="px-3 py-1.5 font-medium">Evidence</th>
                    <th className="px-3 py-1.5 text-right font-medium">Basis</th>
                  </tr>
                </thead>
                <tbody>
                  {signals.map((s) => (
                    <tr key={s.signal_code} className="border-t border-border align-top">
                      <td className="px-3 py-1.5">
                        <div className="font-medium">{s.signal_code}</div>
                        <div className="text-xs text-muted-foreground">{s.name}</div>
                      </td>
                      <td className="px-3 py-1.5"><SeverityBadge value={s.status} /></td>
                      <td className="px-3 py-1.5 text-muted-foreground">{s.evidence}</td>
                      <td className="px-3 py-1.5 text-right text-xs text-muted-foreground capitalize">
                        {s.evidence_basis.replace("-", " ")}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
          {signals && signals.length === 0 && (
            <p className="self-start text-sm text-muted-foreground">
              No signals fired for the latest scored period.
            </p>
          )}
        </div>
      )}
    </div>
  )
}

// -------------------------------------------------------------- alerts queue
function AlertsQueueCard({ tenantId, canManage }: { tenantId: string; canManage: boolean }) {
  const { accessToken } = useSession()
  const [alerts, setAlerts] = useState<LaneCAlertOut[] | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [actingOn, setActingOn] = useState<string | null>(null)

  function load() {
    setLoading(true)
    setError(null)
    listAlerts(tenantId, accessToken!, "new")
      .then((r) => setAlerts(r.alerts))
      .catch((err) => setError(errorMessage(err, "Could not load the alerts queue.")))
      .finally(() => setLoading(false))
  }

  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(() => { load() }, [tenantId])

  function act(alertId: string, status: "reviewed" | "escalated" | "dismissed") {
    setActingOn(alertId)
    reviewAlert(tenantId, alertId, { status }, accessToken!)
      .then(load)
      .catch((err) => setError(errorMessage(err, "Could not update this alert.")))
      .finally(() => setActingOn(null))
  }

  if (loading) return <p className="text-sm text-muted-foreground">Loading alerts…</p>
  if (error) return <Alert variant="destructive"><AlertDescription>{error}</AlertDescription></Alert>
  if (!alerts || alerts.length === 0) {
    return <p className="text-sm text-muted-foreground">No open Lane C alerts for this tenant.</p>
  }

  return (
    <div className="overflow-x-auto rounded-lg border border-border">
      <table className="w-full text-sm">
        <thead className="bg-muted text-left text-muted-foreground">
          <tr>
            <th className="px-3 py-1.5 font-medium">Borrower</th>
            <th className="px-3 py-1.5 font-medium">Period</th>
            <th className="px-3 py-1.5 font-medium">Signal</th>
            <th className="px-3 py-1.5 font-medium">Severity</th>
            <th className="px-3 py-1.5 font-medium">Raised</th>
            <th className="px-3 py-1.5 text-right font-medium">Action</th>
          </tr>
        </thead>
        <tbody>
          {alerts.map((a) => (
            <tr key={a.alert_id} className="border-t border-border align-middle">
              <td className="px-3 py-1.5 font-medium">{a.account}</td>
              <td className="px-3 py-1.5 text-muted-foreground">
                {new Date(a.reporting_date).toLocaleDateString("en-IN")}
              </td>
              <td className="px-3 py-1.5">{a.signal_code}</td>
              <td className="px-3 py-1.5"><SeverityBadge value={a.severity} /></td>
              <td className="px-3 py-1.5 text-muted-foreground">
                {new Date(a.created_at).toLocaleDateString("en-IN")}
              </td>
              <td className="px-3 py-1.5">
                {canManage && <div className="flex justify-end gap-1">
                  <Button size="sm" variant="outline" disabled={actingOn === a.alert_id}
                          onClick={() => act(a.alert_id, "reviewed")}>
                    Reviewed
                  </Button>
                  <Button size="sm" variant="outline" disabled={actingOn === a.alert_id}
                          onClick={() => act(a.alert_id, "escalated")}>
                    Escalate
                  </Button>
                  <Button size="sm" variant="ghost" disabled={actingOn === a.alert_id}
                          onClick={() => act(a.alert_id, "dismissed")}>
                    Dismiss
                  </Button>
                </div>}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

// -------------------------------------------------------------- panel
export function LaneCPanel({ tenantId }: { tenantId: string }) {
  // Reading is one grant, changing another: a role that can only view sees no upload form and
  // no review buttons, rather than controls the server would refuse.
  const { me } = useSession()
  const canManage = hasPermission(me, "lane_c.manage")
  return (
    <div className="space-y-6">
      <PageHeader
        title="Borrower Credit Health"
        description="Lane C — quarterly financial-statement review for corporate borrowers. Distinct from Lane A/B payment monitoring: scheduled, not transaction-triggered."
      />

      {canManage && <UploadStatementCard tenantId={tenantId} />}

      <div className="space-y-2">
        <h6 className="text-sm font-medium">Borrower lookup</h6>
        <BorrowerLookupCard tenantId={tenantId} />
      </div>

      <div className="space-y-2">
        <h6 className="text-sm font-medium">Open alerts</h6>
        <AlertsQueueCard tenantId={tenantId} canManage={canManage} />
      </div>
    </div>
  )
}
