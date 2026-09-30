import { apiFetch, apiUpload } from "./client"

// Mirrors services/lane_c_service/app/routes/lane_c.py exactly - see that file for the
// real request/response shapes. Lane C: quarterly borrower financial-statement review,
// distinct from Lane A (inline) and Lane B (near-real-time) payment monitoring.

export interface IngestStatementOut {
  statement_id: string
  extraction_status: "pending"
  resubmission: boolean
}

export function ingestStatement(
  tenantId: string,
  params: { account: string; reportingDate: string; filingType: string; file: File },
  token: string,
) {
  const form = new FormData()
  form.append("account", params.account)
  form.append("reporting_date", params.reportingDate)
  form.append("filing_type", params.filingType)
  form.append("file", params.file)
  return apiUpload<IngestStatementOut>(`/lane-c/${tenantId}/ingest`, form, token)
}

export interface LaneCScoreOut {
  account: string
  available: boolean
  reporting_date?: string
  score_value?: number
  trend?: "improving" | "stable" | "deteriorating" | "new"
  signal_count?: number
  critical_count?: number
  recommendation?: "continue" | "monitor" | "investigate" | "escalate"
}

export function getLatestScore(tenantId: string, account: string, token: string) {
  return apiFetch<LaneCScoreOut>(
    `/lane-c/${tenantId}/borrowers/${encodeURIComponent(account)}/score`, { token })
}

export interface LaneCSignalOut {
  signal_code: string
  name: string
  observed_value: number
  baseline_value: number | null
  peer_median: number | null
  status: "pass" | "low" | "medium" | "high" | "critical"
  severity: number
  evidence: string
  evidence_basis: "ratio" | "text-pattern" | "structured+notes"
}

export interface LaneCSignalsOut {
  account: string
  reporting_date: string
  signals: LaneCSignalOut[]
}

export function getSignals(tenantId: string, account: string, reportingDate: string, token: string) {
  const p = new URLSearchParams({ reporting_date: reportingDate })
  return apiFetch<LaneCSignalsOut>(
    `/lane-c/${tenantId}/borrowers/${encodeURIComponent(account)}/signals?${p.toString()}`, { token })
}

export interface LaneCAlertOut {
  alert_id: string
  account: string
  reporting_date: string
  signal_code: string
  severity: "critical" | "high" | "medium"
  status: "new" | "reviewed" | "escalated" | "dismissed"
  assigned_to: string
  created_at: string
}

export function listAlerts(tenantId: string, token: string, status?: string) {
  const p = status ? `?${new URLSearchParams({ status }).toString()}` : ""
  return apiFetch<{ alerts: LaneCAlertOut[] }>(`/lane-c/${tenantId}/alerts${p}`, { token })
}

export function reviewAlert(
  tenantId: string,
  alertId: string,
  params: { status: "reviewed" | "escalated" | "dismissed"; notes?: string; assignedTo?: string },
  token: string,
) {
  const form = new FormData()
  form.append("status", params.status)
  if (params.notes) form.append("notes", params.notes)
  if (params.assignedTo) form.append("assigned_to", params.assignedTo)
  return apiUpload<{ alert_id: string; status: string }>(
    `/lane-c/${tenantId}/alerts/${alertId}/review`, form, token)
}
