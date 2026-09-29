import { Search } from "lucide-react"
import { useEffect, useState } from "react"

import { useSession } from "@/app/SessionContext"
import { ApiError } from "@/api/client"
import { listDeliveries, retryAll, retryDelivery, type DeliveryOut } from "@/api/notifications"
import { Alert, AlertDescription } from "@/components/ui/alert"
import { Button } from "@/components/ui/button"
import { Badge } from "@/components/ui/badge"
import { Input } from "@/components/ui/input"

export function DeliveryProblems({ tenantId, refreshToken }: { tenantId: string; refreshToken: number }) {
  const { accessToken } = useSession()
  const [items, setItems] = useState<DeliveryOut[] | null>(null)
  const [counts, setCounts] = useState<Record<string, number>>({})
  const [error, setError] = useState<string | null>(null)
  const [busyId, setBusyId] = useState<string | null>(null)
  const [retryAllBusy, setRetryAllBusy] = useState(false)
  const [query, setQuery] = useState("")

  function reload() {
    listDeliveries(tenantId, accessToken, "problems")
      .then((d) => {
        setItems(d.items)
        setCounts(d.counts)
        setError(null)
      })
      // Say so. An empty panel is indistinguishable from "no failures", which is the
      // most dangerous thing this view could claim while it is actually broken.
      .catch((err) =>
        setError(
          err instanceof ApiError
            ? `Could not load delivery history — ${err.message}. Failures may exist and not be shown here.`
            : "Could not load delivery history. Failures may exist and not be shown here."
        )
      )
  }

  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(reload, [tenantId, accessToken, refreshToken])

  async function handleRetry(id: string) {
    setBusyId(id)
    try {
      await retryDelivery(tenantId, id, accessToken)
      reload()
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not retry this delivery.")
    } finally {
      setBusyId(null)
    }
  }

  async function handleRetryAll() {
    setRetryAllBusy(true)
    try {
      await retryAll(tenantId, accessToken)
      reload()
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not retry deliveries.")
    } finally {
      setRetryAllBusy(false)
    }
  }

  const retrying = counts.retrying || 0
  const gaveUp = counts.abandoned || 0
  const q = query.trim().toLowerCase()
  const filtered = q
    ? items?.filter((p) => {
        const subject = p.subject.toLowerCase()
        const recipient = (p.recipient || p.target || "").toLowerCase()
        const channel = p.channel.toLowerCase()
        return subject.includes(q) || recipient.includes(q) || channel.includes(q)
      })
    : items

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h4 className="text-sm font-medium">
          Delivery problems
          {items && items.length > 0 && (
            <span className="ml-2 font-normal text-muted-foreground">({retrying} retrying, {gaveUp} given up)</span>
          )}
        </h4>
        <div className="flex items-center gap-2">
          {items && items.length > 5 && (
            <div className="relative w-full max-w-[200px]">
              <Search className="pointer-events-none absolute top-1/2 left-2.5 size-3.5 -translate-y-1/2 text-muted-foreground" />
              <Input
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                placeholder="Search subject, recipient, channel"
                className="h-8 pl-8 text-sm"
              />
            </div>
          )}
          {items && items.length > 0 && (
            <Button size="sm" variant="outline" onClick={handleRetryAll} disabled={retryAllBusy}>
              {retryAllBusy ? "Retrying…" : "Retry all"}
            </Button>
          )}
        </div>
      </div>

      {error && (
        <Alert variant="destructive">
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      )}

      {items === null && !error && <p className="text-sm text-muted-foreground">Loading…</p>}

      {items?.length === 0 && (
        <p className="text-sm text-muted-foreground">
          No delivery failures. Everything raised has either been sent or is waiting on a channel you have not enabled.
        </p>
      )}

      {items && items.length > 0 && filtered?.length === 0 && (
        <p className="text-sm text-muted-foreground">No delivery problems match "{query.trim()}".</p>
      )}

      {filtered && filtered.length > 0 && (
        <div className="space-y-2">
          {filtered.map((p) => (
            <div key={p.id} className="flex items-start justify-between gap-4 rounded-md border border-border p-3">
              <div className="min-w-0 space-y-0.5">
                <div className="text-sm font-medium">{p.subject}</div>
                <div className="text-xs text-muted-foreground">
                  {p.channel} → {p.recipient || p.target || "—"} · attempt {p.attempts}
                </div>
                <div className="text-xs text-destructive">{p.last_error || "no detail recorded"}</div>
              </div>
              <div className="flex flex-col items-end gap-1.5">
                <Badge variant={p.status === "pending" ? "secondary" : "destructive"}>
                  {p.status === "pending" ? "retrying" : "given up"}
                </Badge>
                <Button size="sm" variant="ghost" onClick={() => handleRetry(p.id)} disabled={busyId === p.id}>
                  Retry
                </Button>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
