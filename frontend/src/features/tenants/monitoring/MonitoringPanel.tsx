import { useState } from "react"

import { useSession } from "@/app/SessionContext"
import { RENDERED_DASHBOARDS } from "./catalogue"

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

export function MonitoringPanel({ tenantId, initialDashboard }: { tenantId: string; initialDashboard?: string | null }) {
  // The tabs are this role's own dashboards, in the server's order (primary first), not a fixed
  // list of twelve: the server refuses the rest anyway, and showing them is a menu of 403s.
  const { me } = useSession()
  const dashboards = me.dashboards.filter((d) => RENDERED_DASHBOARDS.has(d.key))
  const [chosen, setChosen] = useState(initialDashboard ?? null)
  const active = dashboards.some((d) => d.key === chosen) ? chosen! : dashboards[0]?.key
  const current = dashboards.find((d) => d.key === active)

  if (!current) {
    return <p className="text-sm text-muted-foreground">No dashboards are enabled for your role.</p>
  }

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap gap-1 rounded-lg border border-border bg-muted p-1">
        {dashboards.map((d) => (
          <button
            key={d.key}
            type="button"
            onClick={() => setChosen(d.key)}
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
      <p className="text-sm text-muted-foreground">{current.question}</p>

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
