import { useState } from "react"

import { useSession } from "@/app/SessionContext"
import { ApiError } from "@/api/client"
import type { EvidenceEntity } from "@/api/evidence"
import { fetchExportCsv } from "@/api/exportCsv"
import { Button } from "@/components/ui/button"

export function ExportCsvButton({
  tenantId,
  entity,
  query,
}: {
  tenantId: string
  entity: EvidenceEntity
  query: string
}) {
  const { accessToken } = useSession()
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function handleExport() {
    setError(null)
    setBusy(true)
    try {
      const { blob, filename } = await fetchExportCsv(tenantId, entity, query, accessToken)
      const a = document.createElement("a")
      a.href = URL.createObjectURL(blob)
      a.download = filename
      a.click()
      URL.revokeObjectURL(a.href)
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not export this data.")
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="flex items-center gap-2">
      <Button type="button" size="sm" variant="outline" onClick={handleExport} disabled={busy}>
        {busy ? "Exporting…" : "Export CSV"}
      </Button>
      {error && <span className="text-xs text-destructive">{error}</span>}
    </div>
  )
}
