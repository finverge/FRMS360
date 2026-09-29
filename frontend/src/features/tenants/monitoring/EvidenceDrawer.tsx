import { useEffect, useState } from "react"

import { useSession } from "@/app/SessionContext"
import { ApiError } from "@/api/client"
import { getEvidence, type EvidenceEntity, type EvidenceOut } from "@/api/evidence"
import { Alert, AlertDescription } from "@/components/ui/alert"
import { Badge } from "@/components/ui/badge"
import { Card, CardContent } from "@/components/ui/card"
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { StatCell } from "@/components/ui/sidebar"
import { AiInsightsPanel } from "./AiInsightsPanel"
import { BehavioralBiometricsPanel } from "./BehavioralBiometricsPanel"
import { formatInr } from "./format"
import { MuleRiskPanel } from "./MuleRiskPanel"
import { TransactionLocationPanel } from "./TransactionLocationPanel"
import { PiiRevealControl } from "./PiiRevealControl"
import { RecordCard } from "./RecordCard"
import { DispositionBadge, SeverityBadge } from "./SeverityBadge"

// Stage 6 of the drill: one record with everything needed to defend the decision (see
// routes/dashboards.py's evidence()). Not the full case-lifecycle view the old
// console's evidence panel also carried (workflow/filings/accountability/recovery
// timelines) - that is case-management UI in its own right and a separate,
// larger migration; this is the record + its trail, which is what "evidence" means
// for an alert or a transaction too, not only a case.
export function EvidenceDrawer({
  tenantId,
  entity,
  ident,
  open,
  onOpenChange,
  onViewNetwork,
}: {
  tenantId: string
  entity: EvidenceEntity
  ident: string
  open: boolean
  onOpenChange: (open: boolean) => void
  onViewNetwork?: (caseId: string) => void
}) {
  const { accessToken } = useSession()
  const [data, setData] = useState<EvidenceOut | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [revealed, setRevealed] = useState(false)
  const [justification, setJustification] = useState<string | undefined>()

  function load(reveal: boolean, justification?: string) {
    setError(null)
    getEvidence(tenantId, entity, ident, { reveal, justification }, accessToken)
      .then((d) => {
        if (d.error) {
          setError(d.error)
          return
        }
        setData(d)
        setRevealed(reveal)
        setJustification(justification)
      })
      .catch((err) => setError(err instanceof ApiError ? err.message : "Could not load this record."))
  }

  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(() => load(false), [tenantId, entity, ident, accessToken])

  const alert = data?.alert
  const caseId = entity === "case" ? ident : (alert?.case_id as string | undefined) ?? (data?.case?.case_id as string | undefined)
  const linkedAlerts = data?.sibling_alerts ?? data?.alerts
  // sibling_alerts (alert entity) are OTHER alerts sharing this alert's case - possibly
  // on other transactions entirely. data.alerts (case/transaction entity) are the actual
  // rules this specific case or transaction tripped. Different data, different heading -
  // conflating them would misstate what the table shows.
  const linkedAlertsTitle = entity === "alert" ? "Other alerts on this case" : "Triggered rules"

  const caseRecord = data?.case
  const severity = alert?.severity ?? caseRecord?.severity

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-5xl">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <span className="capitalize">{entity}</span> <span className="font-mono text-sm text-muted-foreground">{ident}</span>
            {data && <Badge variant={revealed ? "destructive" : "outline"}>{revealed ? "PII revealed — audited" : "PII masked"}</Badge>}
          </DialogTitle>
        </DialogHeader>

        {error && (
          <Alert variant="destructive">
            <AlertDescription>{error}</AlertDescription>
          </Alert>
        )}

        {!data && !error && <p className="text-sm text-muted-foreground">Loading…</p>}

        {data && (
          // Verafye-style layout: horizontal tabs over a scrolling main column, plus a
          // rail beside it that stays put - "at a glance" facts and actions the
          // investigator shouldn't have to scroll back up for while reading the rest.
          // Only three tabs, not Verafye's full nine - Device & Session and Investigation
          // are each a couple of fields at most here, so they're sub-cards inside Summary
          // instead of tabs that would look emptier than the ones either side of them.
          <div className="grid gap-4 lg:grid-cols-[1fr_260px] lg:items-start">
            <Tabs defaultValue="summary" className="min-w-0">
              <TabsList>
                <TabsTrigger value="summary">Summary</TabsTrigger>
                <TabsTrigger value="rules">Rules & Decisioning</TabsTrigger>
                <TabsTrigger value="connections">Connections & History</TabsTrigger>
                {data.transaction && <TabsTrigger value="mule-risk">Mule Risk Indicators</TabsTrigger>}
                {data.transaction && <TabsTrigger value="location">Location</TabsTrigger>}
                {data.transaction && <TabsTrigger value="behavioral">Behavioral Biometrics</TabsTrigger>}
                <TabsTrigger value="ai-insights">AI Insights</TabsTrigger>
              </TabsList>

              <div className="mt-3 max-h-[65vh] overflow-y-auto pr-1">
                <TabsContent value="summary" className="mt-0 space-y-4">
                  {/* The trace answers "why", which a score alone cannot - shown first. */}
                  {alert?.matched_reason ? (
                    <div className="rounded-md border border-primary/30 bg-primary/5 p-4">
                      <div className="text-xs font-medium text-muted-foreground">Why this fired</div>
                      <div className="mt-1 flex items-baseline gap-2">
                        <span className="font-mono text-sm font-semibold">{String(alert.rule_id)}</span>
                        <span className="font-mono text-xs text-muted-foreground">{String(alert.sub_rule_ref ?? "")}</span>
                      </div>
                      <div className="mt-1 text-sm">{String(alert.matched_reason)}</div>
                      {alert.observed_value !== null && alert.observed_value !== undefined && (
                        <div className="mt-1 text-xs text-muted-foreground">
                          observed <b className="text-foreground">{String(alert.observed_value)}</b> vs threshold{" "}
                          {String(alert.threshold_value)} {String(alert.observed_unit ?? "")}
                        </div>
                      )}
                      <div className="mt-1 text-xs text-muted-foreground">
                        evaluated under config version <b className="text-foreground">{String(alert.config_version)}</b> — the
                        rules in force at scoring time
                      </div>
                    </div>
                  ) : null}

                  <div className="grid gap-4 sm:grid-cols-2">
                    {data.alert && <RecordCard title="Alert (scored by rule)" record={data.alert} />}
                    {data.transaction && <RecordCard title="Transaction" record={data.transaction} />}
                    {data.case && <RecordCard title="Case" record={data.case} />}
                    {(data.transaction?.device_id != null || data.transaction?.ip_addr != null) && (
                      <RecordCard
                        title="Device & session"
                        record={{ device_id: data.transaction.device_id, ip_addr: data.transaction.ip_addr }}
                      />
                    )}
                  </div>
                </TabsContent>

                <TabsContent value="rules" className="mt-0 space-y-4">
                  {linkedAlerts && linkedAlerts.length > 0 ? (
                    <div className="space-y-2">
                      <h6 className="text-sm font-medium">{linkedAlertsTitle} ({linkedAlerts.length})</h6>
                      <div className="overflow-x-auto rounded-lg border border-border">
                        <table className="w-full text-sm">
                          <thead className="bg-muted text-left text-muted-foreground">
                            <tr>
                              <th className="px-3 py-1.5 font-medium">Rule</th>
                              <th className="px-3 py-1.5 font-medium">Family</th>
                              <th className="px-3 py-1.5 font-medium">Why it fired</th>
                              <th className="px-3 py-1.5 text-center font-medium">Severity</th>
                              <th className="px-3 py-1.5 text-right font-medium">Score</th>
                              <th className="px-3 py-1.5 font-medium">Disposition</th>
                            </tr>
                          </thead>
                          <tbody>
                            {linkedAlerts.map((row, i) => {
                              const ruleId = row.rule_id != null ? String(row.rule_id) : null
                              const subRule = row.sub_rule_ref != null ? String(row.sub_rule_ref) : ""
                              const reason = row.matched_reason != null && row.matched_reason !== ""
                                ? String(row.matched_reason)
                                : null
                              const hasObservation = row.observed_value !== null && row.observed_value !== undefined
                              return (
                                <tr key={i} className="border-t border-border align-top">
                                  <td className="px-3 py-1.5">
                                    <div className="font-mono text-xs font-semibold">{ruleId ?? "—"}{subRule}</div>
                                    {row.alert_id != null && (
                                      <div className="font-mono text-[11px] text-muted-foreground">{String(row.alert_id)}</div>
                                    )}
                                  </td>
                                  <td className="px-3 py-1.5 text-muted-foreground">
                                    {row.rule_family != null ? String(row.rule_family) : "—"}
                                  </td>
                                  <td className="px-3 py-1.5">
                                    {reason ?? <span className="text-muted-foreground">—</span>}
                                    {hasObservation && (
                                      <div className="mt-0.5 text-[11px] text-muted-foreground">
                                        observed <b className="text-foreground">{String(row.observed_value)}</b>
                                        {row.threshold_value != null && <> vs threshold {String(row.threshold_value)}</>}
                                        {row.observed_unit ? ` ${String(row.observed_unit)}` : ""}
                                      </div>
                                    )}
                                  </td>
                                  <td className="px-3 py-1.5 text-center">
                                    {row.severity != null ? <SeverityBadge value={row.severity} /> : "—"}
                                  </td>
                                  <td className="px-3 py-1.5 text-right font-mono text-xs">
                                    {row.score != null ? String(row.score) : "—"}
                                  </td>
                                  <td className="px-3 py-1.5">
                                    {row.disposition != null ? <DispositionBadge value={row.disposition} /> : "—"}
                                  </td>
                                </tr>
                              )
                            })}
                          </tbody>
                        </table>
                      </div>
                      {entity !== "alert" && (
                        <p className="text-xs text-muted-foreground">
                          Only rules that actually fired are recorded — Fraud360 does not persist a
                          pass/fail trace for rules a transaction cleared.
                        </p>
                      )}
                    </div>
                  ) : (
                    <p className="text-sm text-muted-foreground">No other rule activity recorded for this view.</p>
                  )}

                  {data.value_check && (
                    <Alert variant={data.value_check.reconciled ? "success" : "destructive"}>
                      <AlertDescription>
                        {data.value_check.reconciled ? "RECONCILED" : "MISMATCH"} — stated {formatInr(data.value_check.stated_paise)} vs{" "}
                        {formatInr(data.value_check.derived_paise)} derived from linked transactions (invariant R-03).
                      </AlertDescription>
                    </Alert>
                  )}
                </TabsContent>

                <TabsContent value="connections" className="mt-0 space-y-2">
                  {/* Only the case view carries a distinct transactions list today - an
                      alert or transaction opened on its own has no further connections
                      the backend returns, so this stays an honest empty state for those
                      rather than repeating the rules table under a different heading. */}
                  {data.transactions && data.transactions.length > 0 ? (
                    <>
                      <h6 className="text-sm font-medium">Transactions in this case ({data.transactions.length})</h6>
                      <div className="overflow-x-auto rounded-lg border border-border">
                        <table className="w-full text-sm">
                          <thead className="bg-muted text-left text-muted-foreground">
                            <tr>
                              <th className="px-3 py-1.5 font-medium">Txn</th>
                              <th className="px-3 py-1.5 font-medium">Rail</th>
                              <th className="px-3 py-1.5 text-right font-medium">Amount</th>
                              <th className="px-3 py-1.5 font-medium">Debtor</th>
                              <th className="px-3 py-1.5 font-medium">Creditor</th>
                              <th className="px-3 py-1.5 font-medium">Status</th>
                            </tr>
                          </thead>
                          <tbody>
                            {data.transactions.map((row, i) => (
                              <tr key={i} className="border-t border-border">
                                <td className="px-3 py-1.5 font-mono text-xs">{String(row.txn_id ?? "—")}</td>
                                <td className="px-3 py-1.5">{String(row.rail ?? "—")}</td>
                                <td className="px-3 py-1.5 text-right">
                                  {typeof row.amount_paise === "number" ? formatInr(row.amount_paise) : "—"}
                                </td>
                                <td className="px-3 py-1.5 font-mono text-xs text-muted-foreground">{String(row.debtor_account ?? "—")}</td>
                                <td className="px-3 py-1.5 font-mono text-xs text-muted-foreground">{String(row.creditor_account ?? "—")}</td>
                                <td className="px-3 py-1.5">{String(row.status ?? "—")}</td>
                              </tr>
                            ))}
                          </tbody>
                        </table>
                      </div>
                    </>
                  ) : (
                    <p className="text-sm text-muted-foreground">
                      No additional connection or transaction history is available for this view.
                    </p>
                  )}
                </TabsContent>

                {data.transaction && (
                  <TabsContent value="mule-risk" className="mt-0">
                    <MuleRiskPanel
                      tenantId={tenantId}
                      debtorAccount={data.transaction.debtor_account != null ? String(data.transaction.debtor_account) : undefined}
                      creditorAccount={data.transaction.creditor_account != null ? String(data.transaction.creditor_account) : undefined}
                      revealed={revealed}
                    />
                  </TabsContent>
                )}

                {data.transaction && (
                  <TabsContent value="location" className="mt-0">
                    <TransactionLocationPanel
                      tenantId={tenantId}
                      txnId={String(data.transaction.txn_id)}
                      revealed={revealed}
                      justification={justification}
                    />
                  </TabsContent>
                )}

                {data.transaction && (
                  <TabsContent value="behavioral" className="mt-0">
                    <BehavioralBiometricsPanel transaction={data.transaction} />
                  </TabsContent>
                )}

                <TabsContent value="ai-insights" className="mt-0">
                  <AiInsightsPanel
                    tenantId={tenantId}
                    entity={entity}
                    ident={ident}
                    revealed={revealed}
                    justification={justification}
                  />
                </TabsContent>
              </div>
            </Tabs>

            <div className="space-y-3">
              <Card className="py-3">
                <CardContent className="space-y-2.5 px-4">
                  <div className="flex items-center justify-between">
                    <span className="text-[11px] font-medium tracking-wide text-muted-foreground uppercase">At a glance</span>
                    {severity != null && <SeverityBadge value={severity} />}
                  </div>
                  {alert?.disposition != null && (
                    <StatCell label="Disposition" value={<DispositionBadge value={alert.disposition} />} />
                  )}
                  {alert?.rule_id != null && <StatCell label="Rule" value={String(alert.rule_id)} />}
                  {alert?.config_version != null && (
                    <StatCell label="Config version" value={String(alert.config_version)} />
                  )}
                </CardContent>
              </Card>

              {caseId && (
                <Card className="py-3">
                  <CardContent className="space-y-2 px-4">
                    <div className="text-[11px] font-medium tracking-wide text-muted-foreground uppercase">Case</div>
                    <StatCell label="Case ID" value={caseId} />
                    {caseRecord?.state != null && <StatCell label="State" value={String(caseRecord.state)} />}
                    <StatCell
                      label="Assignee"
                      value={caseRecord?.assignee ? String(caseRecord.assignee) : "Unassigned"}
                    />
                    {onViewNetwork && (
                      <button
                        type="button"
                        className="pt-1 text-sm text-primary hover:underline"
                        onClick={() => onViewNetwork(caseId)}
                      >
                        View network →
                      </button>
                    )}
                  </CardContent>
                </Card>
              )}

              <Card className="py-3">
                <CardContent className="px-4">
                  <PiiRevealControl onApply={(justification) => load(true, justification)} disabled={revealed} />
                </CardContent>
              </Card>
            </div>
          </div>
        )}
      </DialogContent>
    </Dialog>
  )
}
