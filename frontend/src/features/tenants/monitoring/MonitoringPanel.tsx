import { useState } from "react"

import { Account360Dashboard } from "./Account360Dashboard"
import { AmlDashboard } from "./AmlDashboard"
import { AnalystDashboard } from "./AnalystDashboard"
import { BoardDashboard } from "./BoardDashboard"
import { EwsDashboard } from "./EwsDashboard"
import { InspectionDashboard } from "./InspectionDashboard"
import { InvestigatorDashboard } from "./InvestigatorDashboard"
import { ModelDashboard } from "./ModelDashboard"
import { OperationsDashboard } from "./OperationsDashboard"
import { RealtimeDashboard } from "./RealtimeDashboard"
import { RfaDashboard } from "./RfaDashboard"
import { SupervisorDashboard } from "./SupervisorDashboard"

// Ordered by the spec's delivery priority (P1, then P2, then P3) - same order as
// cp_common.rbac.DASHBOARDS minus tenant_health, which is platform-operations
// telemetry, not a bank's own view. All twelve are now migrated.
const DASHBOARDS = [
  { key: "analyst", label: "Analyst", persona: "L1 Fraud Analyst" },
  { key: "ews", label: "EWS Signals", persona: "Fraud Risk Manager / Analyst" },
  { key: "rfa", label: "RFA Lifecycle", persona: "Investigator / Compliance" },
  { key: "board", label: "Board", persona: "CRO / ACB / Special Committee" },
  { key: "supervisor", label: "Compliance", persona: "Compliance Supervisor" },
  { key: "realtime", label: "Real-Time", persona: "Operations" },
  { key: "aml", label: "AML / STR", persona: "Principal Officer (PMLA)" },
  { key: "account360", label: "Account 360", persona: "Investigator" },
  { key: "investigator", label: "Investigator", persona: "L2 Senior Investigator" },
  { key: "model", label: "Model Risk", persona: "Model Risk / Data Science" },
  { key: "risk_manager", label: "Operations", persona: "Fraud Risk Manager" },
  { key: "inspection", label: "Inspection", persona: "Internal Audit / RBI Inspection" },
]

export function MonitoringPanel({ tenantId }: { tenantId: string }) {
  const [active, setActive] = useState("analyst")
  const current = DASHBOARDS.find((d) => d.key === active)!

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap gap-1 rounded-lg border border-border bg-muted p-1">
        {DASHBOARDS.map((d) => (
          <button
            key={d.key}
            type="button"
            onClick={() => setActive(d.key)}
            className={
              "rounded-md px-3 py-1.5 text-sm font-medium whitespace-nowrap transition-colors " +
              (active === d.key
                ? "bg-primary text-primary-foreground shadow-sm"
                : "text-muted-foreground hover:bg-accent hover:text-accent-foreground")
            }
          >
            {d.label}
          </button>
        ))}
      </div>
      <p className="text-xs font-medium tracking-wide text-muted-foreground uppercase">{current.persona}</p>

      {active === "analyst" && <AnalystDashboard tenantId={tenantId} />}
      {active === "ews" && <EwsDashboard tenantId={tenantId} />}
      {active === "rfa" && <RfaDashboard tenantId={tenantId} />}
      {active === "board" && <BoardDashboard tenantId={tenantId} />}
      {active === "supervisor" && <SupervisorDashboard tenantId={tenantId} />}
      {active === "realtime" && <RealtimeDashboard tenantId={tenantId} />}
      {active === "aml" && <AmlDashboard tenantId={tenantId} />}
      {active === "account360" && <Account360Dashboard tenantId={tenantId} />}
      {active === "investigator" && <InvestigatorDashboard tenantId={tenantId} />}
      {active === "model" && <ModelDashboard tenantId={tenantId} />}
      {active === "risk_manager" && <OperationsDashboard tenantId={tenantId} />}
      {active === "inspection" && <InspectionDashboard tenantId={tenantId} />}
    </div>
  )
}
