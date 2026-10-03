// The work that can be waiting for someone, and which dashboard it is read from.
//
// A queue shows on Home only when (a) the figure is above zero and (b) the role may open the
// dashboard behind it - so nobody is shown a number they cannot click through to. Several
// dashboards carry the same figure (the board and supervisor views both report overdue
// filings); `from` lists them most-preferred first, and pickSources() takes one the role can
// open, preferring a dashboard already being fetched, so a role needs as few calls as possible.
import type { MeOut } from "@/api/auth"
import { canOpenDashboard, primaryDashboard } from "@/lib/access"

export interface QueueDef {
  metric: string
  label: string
  why: string
  tone: "alert" | "warn"
  from: string[]
}

export const QUEUES: QueueDef[] = [
  { metric: "sla_breach_count", label: "Alerts past their response time", why: "Open alerts that have missed the response target.", tone: "alert", from: ["risk_manager"] },
  { metric: "nj_breach_count", label: "Natural-justice deadlines breached", why: "A show-cause or reasoned-order step is past its date.", tone: "alert", from: ["board", "supervisor", "rfa"] },
  { metric: "fraud_without_reasoned_order", label: "Fraud classifications without a reasoned order", why: "Classified as fraud with no reasoned order on record.", tone: "alert", from: ["rfa"] },
  { metric: "str_overdue_count", label: "STRs overdue", why: "Suspicious transaction reports past their FIU-IND filing date.", tone: "alert", from: ["board", "supervisor", "aml"] },
  { metric: "fmr_overdue_count", label: "FMRs overdue", why: "Fraud monitoring reports past their RBI filing date.", tone: "alert", from: ["board", "supervisor"] },
  { metric: "untouched_alert_count", label: "Alerts nobody has opened", why: "Raised, but no one has picked them up yet.", tone: "warn", from: ["risk_manager"] },
  { metric: "pending_alert_count", label: "Alerts waiting for review", why: "Raised and not yet decided as real or false.", tone: "warn", from: ["analyst", "risk_manager"] },
  { metric: "rfa_awaiting_show_cause", label: "Accounts awaiting a show-cause notice", why: "Red-flagged, with the notice not yet issued.", tone: "warn", from: ["rfa"] },
  { metric: "stalled_case_count", label: "Stalled cases", why: "Open cases with no movement.", tone: "warn", from: ["rfa"] },
  { metric: "open_case_count", label: "Open cases", why: "Investigations still in progress.", tone: "warn", from: ["investigator", "risk_manager", "rfa", "supervisor"] },
]

/** Which dashboards Home must fetch for this role, and which one serves each queue. The
 * primary dashboard is always first: its figures are the headline tiles. */
export function pickSources(me: MeOut): { dashboards: string[]; queueSource: Map<string, string> } {
  const chosen: string[] = []
  const primary = primaryDashboard(me)
  if (primary) chosen.push(primary)
  const queueSource = new Map<string, string>()
  for (const q of QUEUES) {
    const open = q.from.filter((d) => canOpenDashboard(me, d))
    if (open.length === 0) continue
    const source = open.find((d) => chosen.includes(d)) ?? open[0]
    if (!chosen.includes(source)) chosen.push(source)
    queueSource.set(q.metric, source)
  }
  return { dashboards: chosen, queueSource }
}
