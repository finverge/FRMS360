import { useState } from "react"

import { useSession } from "@/app/SessionContext"
import { ApiError } from "@/api/client"
import {
  exportTenant,
  promoteTenant,
  transitionTenant,
  type LifecycleAction,
  type TenantOut,
} from "@/api/tenants"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Alert, AlertDescription } from "@/components/ui/alert"
import { AlertCircle } from "lucide-react"

// tenant.status values, per services/tenant_service/app/models.py's comment:
// provisioning|active|suspended|degraded, plus offboarded via the offboard transition.
const STATUS_VARIANT: Record<string, "default" | "secondary" | "destructive" | "success" | "outline"> = {
  active: "success",
  provisioning: "secondary",
  degraded: "destructive",
  suspended: "destructive",
  offboarded: "outline",
}

export function TenantHeader({
  tenant,
  onChanged,
}: {
  tenant: TenantOut
  onChanged: (tenant: TenantOut) => void
}) {
  const { accessToken, isPlatformAdmin } = useSession()
  const [busy, setBusy] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [exported, setExported] = useState<Record<string, unknown> | null>(null)

  async function runLifecycle(action: LifecycleAction) {
    setBusy(action)
    setError(null)
    try {
      onChanged(await transitionTenant(tenant.id, action, accessToken))
    } catch (err) {
      setError(err instanceof ApiError ? err.message : `Could not ${action} this tenant.`)
    } finally {
      setBusy(null)
    }
  }

  async function runPromote() {
    setBusy("promote")
    setError(null)
    try {
      onChanged(await promoteTenant(tenant.id, accessToken))
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not promote this tenant.")
    } finally {
      setBusy(null)
    }
  }

  async function runExport() {
    setBusy("export")
    setError(null)
    try {
      setExported(await exportTenant(tenant.id, accessToken))
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not export this tenant's record.")
    } finally {
      setBusy(null)
    }
  }

  function downloadExport() {
    if (!exported) return
    const blob = new Blob([JSON.stringify(exported, null, 2)], { type: "application/json" })
    const a = document.createElement("a")
    a.href = URL.createObjectURL(blob)
    a.download = `${tenant.slug}-export.json`
    a.click()
    URL.revokeObjectURL(a.href)
  }

  return (
    <div className="mb-4 space-y-2.5 border-b border-border pb-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-3">
          <h1 className="text-xl font-semibold">{tenant.display_name}</h1>
          <Badge variant={STATUS_VARIANT[tenant.status] ?? "outline"}>{tenant.status}</Badge>
          {tenant.is_sandbox && <Badge variant="outline">sandbox</Badge>}
        </div>

        {isPlatformAdmin && (
          <div className="flex flex-wrap gap-2">
            {tenant.is_sandbox && tenant.status === "active" && (
              <Button size="sm" variant="outline" disabled={!!busy} onClick={runPromote}>
                Promote to production
              </Button>
            )}
            {(tenant.status === "active" || tenant.status === "degraded") && (
              <Button size="sm" variant="outline" disabled={!!busy} onClick={() => runLifecycle("suspend")}>
                Suspend
              </Button>
            )}
            {tenant.status === "suspended" && (
              <Button size="sm" variant="outline" disabled={!!busy} onClick={() => runLifecycle("resume")}>
                Resume
              </Button>
            )}
            <Button size="sm" variant="outline" disabled={!!busy} onClick={runExport}>
              Export
            </Button>
            {tenant.status !== "offboarded" && (
              <Button size="sm" variant="destructive" disabled={!!busy} onClick={() => runLifecycle("offboard")}>
                Offboard
              </Button>
            )}
          </div>
        )}
      </div>

      {error && (
        <Alert variant="destructive">
          <AlertCircle />
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      )}

      {exported && (
        <Alert variant={exported.complete ? "success" : "destructive"}>
          <AlertDescription className="flex items-center justify-between gap-4">
            <span>
              {exported.complete
                ? "Export ready - the full fraud record and configuration."
                : "Export ready, but incomplete - see problems in the downloaded file."}
            </span>
            <Button size="sm" variant="outline" onClick={downloadExport}>
              Download JSON
            </Button>
          </AlertDescription>
        </Alert>
      )}
    </div>
  )
}
