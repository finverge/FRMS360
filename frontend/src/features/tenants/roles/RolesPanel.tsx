import { useEffect, useState } from "react"
import { AlertCircle } from "lucide-react"

import { useSession } from "@/app/SessionContext"
import { ApiError } from "@/api/client"
import { confirmRoleElevation, deleteRole, listRoles, type RoleOut } from "@/api/tenants"
import { Alert, AlertDescription } from "@/components/ui/alert"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card, CardContent } from "@/components/ui/card"
import { PageHeader, StatCell } from "@/components/ui/sidebar"
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
import { RoleForm } from "./RoleForm"
import { FIXED_ROLE_NAMES } from "./roleCatalogue"

export function RolesPanel({ tenantId }: { tenantId: string }) {
  const { accessToken } = useSession()
  const [roles, setRoles] = useState<RoleOut[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [busyName, setBusyName] = useState<string | null>(null)
  const [formOpen, setFormOpen] = useState(false)
  const [editing, setEditing] = useState<RoleOut | undefined>(undefined)

  function reload() {
    listRoles(tenantId, accessToken)
      .then(setRoles)
      .catch((err) => setError(err instanceof ApiError ? err.message : "Could not load roles."))
  }

  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(reload, [tenantId, accessToken])

  function openNew() {
    setEditing(undefined)
    setFormOpen(true)
  }

  function openEdit(role: RoleOut) {
    setEditing(role)
    setFormOpen(true)
  }

  // BR-715-style maker-checker: a different eligible actor than the one who staged the
  // elevation must confirm it (roles.py's confirm_elevation - self-approval is refused
  // server-side, and that refusal's message is what surfaces here on error).
  async function handleConfirm(role: RoleOut) {
    setError(null)
    setBusyName(role.name)
    try {
      await confirmRoleElevation(tenantId, role.name, accessToken)
      reload()
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not confirm this elevation.")
    } finally {
      setBusyName(null)
    }
  }

  async function handleDelete(role: RoleOut) {
    setError(null)
    setBusyName(role.name)
    try {
      await deleteRole(tenantId, role.name, accessToken)
      reload()
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not delete this role.")
    } finally {
      setBusyName(null)
    }
  }

  const customCount = roles?.filter((r) => r.source === "custom").length
  const pendingCount = roles?.filter((r) => r.elevation_pending).length
  const totalMembers = roles?.reduce((sum, r) => sum + r.member_count, 0)

  return (
    <div className="space-y-4">
      <PageHeader
        title="Roles"
        description="What each role can do, and how many of this tenant's own users hold it"
        action={<Button size="sm" onClick={openNew}>+ New role</Button>}
      />

      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        <Card className="py-3"><CardContent className="px-4"><StatCell label="Roles" value={roles?.length ?? "—"} /></CardContent></Card>
        <Card className="py-3"><CardContent className="px-4"><StatCell label="Custom roles" value={customCount ?? "—"} /></CardContent></Card>
        <Card className="py-3"><CardContent className="px-4"><StatCell label="Pending confirm" value={pendingCount ?? "—"} tone={pendingCount ? "warning" : "default"} /></CardContent></Card>
        <Card className="py-3"><CardContent className="px-4"><StatCell label="Total members" value={totalMembers ?? "—"} /></CardContent></Card>
      </div>

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
              <th className="px-3 py-1.5 font-medium">Role</th>
              <th className="px-3 py-1.5 font-medium">Modules</th>
              <th className="px-3 py-1.5 text-center font-medium">Admin</th>
              <th className="px-3 py-1.5 text-center font-medium">PII</th>
              <th className="px-3 py-1.5 text-center font-medium">Activate</th>
              <th className="px-3 py-1.5 text-right font-medium">Members</th>
              <th className="px-3 py-1.5" />
            </tr>
          </thead>
          <tbody>
            {roles === null && (
              <tr><td colSpan={7} className="px-3 py-2.5 text-muted-foreground">Loading…</td></tr>
            )}
            {roles?.length === 0 && (
              <tr><td colSpan={7} className="px-3 py-2.5 text-muted-foreground">No roles</td></tr>
            )}
            {roles?.map((r) => {
              const fixed = FIXED_ROLE_NAMES.has(r.name)
              const canDelete = !fixed && r.member_count === 0
              return (
                <tr key={r.name} className="border-t border-border align-top">
                  <td className="px-3 py-1.5">
                    <div className="flex flex-wrap items-center gap-1.5">
                      <span className="font-medium">{r.label}</span>
                      <span className="font-mono text-xs text-muted-foreground">{r.name}</span>
                      {r.source === "custom" && <Badge variant="outline">custom</Badge>}
                      {r.elevation_pending && (
                        <Badge
                          variant="secondary"
                          title="A different administrator must confirm this before it takes effect"
                        >
                          pending confirm
                        </Badge>
                      )}
                    </div>
                  </td>
                  <td className="px-3 py-1.5 text-muted-foreground">
                    {r.modules.map((m) => m.label).join(", ") || "—"}
                  </td>
                  <td className="px-3 py-1.5 text-center">{r.can_admin_tenant ? "✓" : "—"}</td>
                  <td className="px-3 py-1.5 text-center">{r.can_reveal_pii ? "✓" : "—"}</td>
                  <td className="px-3 py-1.5 text-center" title="May propose or confirm a configuration activation">
                    {r.can_activate_config ? "✓" : "—"}
                  </td>
                  <td className="px-3 py-1.5 text-right">{r.member_count}</td>
                  <td className="px-3 py-1.5 text-right">
                    <div className="flex flex-wrap justify-end gap-x-3 gap-y-1">
                      <button type="button" className="text-sm text-primary hover:underline" onClick={() => openEdit(r)}>
                        Edit
                      </button>
                      {r.elevation_pending && (
                        <button
                          type="button"
                          className="text-sm text-primary hover:underline disabled:opacity-50"
                          disabled={busyName === r.name}
                          onClick={() => handleConfirm(r)}
                        >
                          Confirm
                        </button>
                      )}
                      {fixed ? (
                        <span className="text-sm text-muted-foreground" title="One of the platform's fixed roles">fixed</span>
                      ) : r.member_count > 0 ? (
                        <span className="text-sm text-muted-foreground" title="Reassign its users first">in use</span>
                      ) : (
                        <AlertDialog>
                          <AlertDialogTrigger asChild>
                            <button type="button" className="text-sm text-destructive hover:underline" disabled={!canDelete}>
                              Delete
                            </button>
                          </AlertDialogTrigger>
                          <AlertDialogContent>
                            <AlertDialogHeader>
                              <AlertDialogTitle>Delete {r.label}?</AlertDialogTitle>
                              <AlertDialogDescription>
                                This cannot be undone. No user currently holds this role, so nothing is reassigned.
                              </AlertDialogDescription>
                            </AlertDialogHeader>
                            <AlertDialogFooter>
                              <AlertDialogCancel>Cancel</AlertDialogCancel>
                              <AlertDialogAction onClick={() => handleDelete(r)}>Delete</AlertDialogAction>
                            </AlertDialogFooter>
                          </AlertDialogContent>
                        </AlertDialog>
                      )}
                    </div>
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>

      {/* Mounted only while open, not just keyed by role name - closing and reopening
          the SAME role (e.g. after a propose, to check the current state again) must
          re-seed the form's checkboxes from the latest fetched row, not resume
          whatever the operator last toggled in a previous, already-closed session. */}
      {formOpen && (
        <RoleForm
          tenantId={tenantId}
          role={editing}
          open={formOpen}
          onOpenChange={setFormOpen}
          onSaved={reload}
        />
      )}
    </div>
  )
}
