import { useEffect, useState } from "react"
import { AlertCircle } from "lucide-react"

import { useSession } from "@/app/SessionContext"
import { ApiError } from "@/api/client"
import { deleteProvider, listProviders, type IdpOut } from "@/api/identityProviders"
import { Alert, AlertDescription } from "@/components/ui/alert"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { PageHeader } from "@/components/ui/sidebar"
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
  AlertDialogTrigger,
} from "@/components/ui/alert-dialog"
import { IdpForm } from "./IdpForm"

export function IdentityProvidersPanel({ tenantId }: { tenantId: string }) {
  const { accessToken } = useSession()
  const [providers, setProviders] = useState<IdpOut[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [busyId, setBusyId] = useState<string | null>(null)
  const [formOpen, setFormOpen] = useState(false)
  const [editing, setEditing] = useState<IdpOut | undefined>(undefined)

  function reload() {
    listProviders(tenantId, accessToken)
      .then(setProviders)
      .catch((err) => setError(err instanceof ApiError ? err.message : "Could not load identity providers."))
  }

  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(reload, [tenantId, accessToken])

  function openNew() {
    setEditing(undefined)
    setFormOpen(true)
  }

  function openEdit(p: IdpOut) {
    setEditing(p)
    setFormOpen(true)
  }

  async function handleDelete(p: IdpOut) {
    setError(null)
    setBusyId(p.id)
    try {
      await deleteProvider(tenantId, p.id, accessToken)
      reload()
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not delete this provider.")
    } finally {
      setBusyId(null)
    }
  }

  return (
    <div className="space-y-4">
      <PageHeader
        title="Identity providers"
        description="Federated sign-in — sign-in is by platform password only when none is configured"
        action={<Button size="sm" onClick={openNew}>+ Add provider</Button>}
      />

      {error && (
        <Alert variant="destructive">
          <AlertCircle />
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      )}

      <div className="overflow-x-auto rounded-lg border border-border">
        <table className="w-full text-sm">
          <thead className="bg-muted text-left text-muted-foreground">
            <tr>
              <th className="px-3 py-1.5 font-medium">Provider</th>
              <th className="px-3 py-1.5 font-medium">Protocol</th>
              <th className="px-3 py-1.5 font-medium">Default role</th>
              <th className="px-3 py-1.5 font-medium">Status</th>
              <th className="px-3 py-1.5" />
            </tr>
          </thead>
          <tbody>
            {providers === null && (
              <tr><td colSpan={5} className="px-3 py-2.5 text-muted-foreground">Loading…</td></tr>
            )}
            {providers?.length === 0 && (
              <tr><td colSpan={5} className="px-3 py-2.5 text-muted-foreground">No identity providers configured — sign-in is by platform password only</td></tr>
            )}
            {providers?.map((p) => (
              <tr key={p.id} className="border-t border-border align-top">
                <td className="px-3 py-1.5">
                  <div className="font-medium">{p.display_name}</div>
                  <div className="font-mono text-xs text-muted-foreground">{p.slug}</div>
                </td>
                <td className="px-3 py-1.5 uppercase">{p.protocol}</td>
                <td className="px-3 py-1.5 text-muted-foreground">{p.default_role}</td>
                <td className="px-3 py-1.5">
                  <Badge variant={p.enabled ? "success" : "outline"}>{p.enabled ? "enabled" : "disabled"}</Badge>
                </td>
                <td className="px-3 py-1.5 text-right">
                  <div className="flex flex-wrap justify-end gap-x-3 gap-y-1">
                    <button type="button" className="text-sm text-primary hover:underline" onClick={() => openEdit(p)}>
                      Edit
                    </button>
                    <AlertDialog>
                      <AlertDialogTrigger asChild>
                        <button type="button" className="text-sm text-destructive hover:underline" disabled={busyId === p.id}>
                          Delete
                        </button>
                      </AlertDialogTrigger>
                      <AlertDialogContent>
                        <AlertDialogHeader>
                          <AlertDialogTitle>Delete {p.display_name}?</AlertDialogTitle>
                          <AlertDialogDescription>
                            Users who sign in through this provider will no longer be able to, until it — or another — is configured again. This cannot be undone.
                          </AlertDialogDescription>
                        </AlertDialogHeader>
                        <AlertDialogFooter>
                          <AlertDialogCancel>Cancel</AlertDialogCancel>
                          <AlertDialogAction onClick={() => handleDelete(p)}>Delete</AlertDialogAction>
                        </AlertDialogFooter>
                      </AlertDialogContent>
                    </AlertDialog>
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {formOpen && (
        <IdpForm
          tenantId={tenantId}
          provider={editing}
          open={formOpen}
          onOpenChange={setFormOpen}
          onSaved={reload}
        />
      )}
    </div>
  )
}
