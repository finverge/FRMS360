import { ApiError } from "./client"
import type { EvidenceEntity } from "./evidence"

const API_BASE = "/api"

// Mirrors services/analytics_service/app/routes/dashboards.py's export_rows() -
// watermarked, row-capped, masked-by-default CSV that leaves an audit trail of its own.
// Not JSON, so this bypasses apiFetch() and reads the Content-Disposition filename the
// server names it, rather than inventing one client-side.
export async function fetchExportCsv(
  tenantId: string,
  entity: EvidenceEntity,
  query: string,
  token: string
): Promise<{ blob: Blob; filename: string }> {
  const res = await fetch(`${API_BASE}/analytics/${tenantId}/export/${entity}${query ? `?${query}` : ""}`, {
    headers: token ? { Authorization: `Bearer ${token}` } : {},
  })
  if (!res.ok) {
    const text = await res.text()
    let message = res.statusText
    let code = "unknown_error"
    try {
      const data = JSON.parse(text)
      message = data?.error?.message ?? message
      code = data?.error?.code ?? code
    } catch {
      // not JSON - a plain-text or empty error body
    }
    throw new ApiError(message, code, res.status)
  }
  const disposition = res.headers.get("content-disposition") ?? ""
  const match = /filename="?([^";]+)"?/.exec(disposition)
  const filename = match?.[1] ?? `${entity}-export.csv`
  const blob = await res.blob()
  return { blob, filename }
}
