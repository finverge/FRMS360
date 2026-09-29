import { useEffect, useState } from "react"

import { useSession } from "@/app/SessionContext"
import { ApiError } from "@/api/client"
import {
  deleteView,
  listViewAudiences,
  listViews,
  saveView,
  type SavedViewOut,
  type ViewAudience,
} from "@/api/savedViews"
import { Alert, AlertDescription } from "@/components/ui/alert"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"

// A saved view is "this dashboard, with these filters" (see routes/views.py) - scoped
// to the current persona's dashboard key so Analyst's views don't clutter EWS's list
// and vice versa. Only the date-range/severity filters this migration has wired so far
// are captured; the fuller ~20-field set isn't in a saved view's query any more than
// it's in the filter bar above it yet.
export function SavedViewsBar({
  tenantId,
  dashboardKey,
  currentQuery,
  onApply,
}: {
  tenantId: string
  dashboardKey: string
  currentQuery: string
  onApply: (query: string) => void
}) {
  const { accessToken } = useSession()
  const [views, setViews] = useState<SavedViewOut[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [formOpen, setFormOpen] = useState(false)

  function reload() {
    listViews(tenantId, accessToken)
      .then((rows) => setViews(rows.filter((v) => v.dashboard === dashboardKey)))
      .catch((err) => setError(err instanceof ApiError ? err.message : "Could not load saved views."))
  }

  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(reload, [tenantId, dashboardKey, accessToken])

  async function handleDelete(id: string) {
    setError(null)
    try {
      await deleteView(tenantId, id, accessToken)
      reload()
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not delete this view.")
    }
  }

  return (
    <div className="flex flex-wrap items-center gap-2">
      <span className="text-xs font-medium text-muted-foreground">Views</span>
      {views === null && <span className="text-xs text-muted-foreground">…</span>}
      {views?.map((v) => (
        <div key={v.id} className="flex items-center gap-1 rounded-md border border-border py-0.5 pr-1 pl-2 text-xs">
          <button type="button" className="hover:underline" onClick={() => onApply(v.query)} title={v.audience}>
            {v.name}
          </button>
          {v.shared && <Badge variant="outline" className="px-1 py-0 text-[10px]">{v.mine ? "shared" : v.audience}</Badge>}
          {v.mine && (
            <button
              type="button"
              className="text-muted-foreground hover:text-destructive"
              aria-label={`Delete ${v.name}`}
              onClick={() => handleDelete(v.id)}
            >
              ×
            </button>
          )}
        </div>
      ))}
      <Button size="sm" variant="ghost" onClick={() => setFormOpen(true)}>+ Save current view</Button>

      {error && (
        <Alert variant="destructive" className="w-full">
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      )}

      {formOpen && (
        <SaveViewForm
          tenantId={tenantId}
          dashboardKey={dashboardKey}
          query={currentQuery}
          open={formOpen}
          onOpenChange={setFormOpen}
          onSaved={reload}
        />
      )}
    </div>
  )
}

function SaveViewForm({
  tenantId,
  dashboardKey,
  query,
  open,
  onOpenChange,
  onSaved,
}: {
  tenantId: string
  dashboardKey: string
  query: string
  open: boolean
  onOpenChange: (open: boolean) => void
  onSaved: () => void
}) {
  const { accessToken } = useSession()
  const [name, setName] = useState("")
  const [shared, setShared] = useState(false)
  const [roles, setRoles] = useState<string[]>([])
  const [audiences, setAudiences] = useState<ViewAudience[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    listViewAudiences(tenantId, accessToken).then(setAudiences).catch(() => setAudiences([]))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tenantId, accessToken])

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    setError(null)
    setBusy(true)
    try {
      await saveView(tenantId, { name, dashboard: dashboardKey, query, shared, shared_roles: roles }, accessToken)
      onSaved()
      onOpenChange(false)
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not save this view.")
    } finally {
      setBusy(false)
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Save current view</DialogTitle>
        </DialogHeader>
        <form onSubmit={handleSubmit} className="space-y-4">
          <div className="space-y-2">
            <Label htmlFor="sv-name">Name</Label>
            <Input id="sv-name" value={name} onChange={(e) => setName(e.target.value)} required />
          </div>
          <label className="flex items-center gap-2 text-sm">
            <input type="checkbox" checked={shared} onChange={(e) => setShared(e.target.checked)} />
            Share with other users of this tenant
          </label>
          {shared && (
            <div className="space-y-2">
              <Label>Visible to</Label>
              <div className="flex flex-wrap gap-3">
                {(audiences ?? []).map((a) => (
                  <label key={a.name} className="flex items-center gap-1.5 text-sm">
                    <input
                      type="checkbox"
                      checked={roles.includes(a.name)}
                      onChange={(e) =>
                        setRoles(e.target.checked ? [...roles, a.name] : roles.filter((r) => r !== a.name))
                      }
                    />
                    {a.label}
                  </label>
                ))}
              </div>
              <p className="text-xs text-muted-foreground">Leave every box unchecked to share with everyone in this tenant.</p>
            </div>
          )}
          {error && (
            <Alert variant="destructive">
              <AlertDescription>{error}</AlertDescription>
            </Alert>
          )}
          <DialogFooter>
            <Button type="button" variant="ghost" onClick={() => onOpenChange(false)}>Cancel</Button>
            <Button type="submit" disabled={busy}>{busy ? "Saving…" : "Save"}</Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  )
}
