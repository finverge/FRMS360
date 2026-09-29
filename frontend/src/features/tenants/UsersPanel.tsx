import { useEffect, useState } from "react"
import { AlertCircle, Mail, Search, Trash2 } from "lucide-react"

import { useSession } from "@/app/SessionContext"
import { ApiError } from "@/api/client"
import {
  inviteUser,
  listRoles,
  listUsers,
  removeUser,
  updateUserRole,
  type RoleOut,
  type TenantUserOut,
} from "@/api/tenants"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Alert, AlertDescription } from "@/components/ui/alert"
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

export function UsersPanel({ tenantId }: { tenantId: string }) {
  const { accessToken } = useSession()
  const [users, setUsers] = useState<TenantUserOut[] | null>(null)
  const [roles, setRoles] = useState<RoleOut[]>([])
  const [email, setEmail] = useState("")
  const [role, setRole] = useState("analyst")
  const [error, setError] = useState<string | null>(null)
  const [inviteNotice, setInviteNotice] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [query, setQuery] = useState("")

  function reload() {
    listUsers(tenantId, accessToken)
      .then(setUsers)
      .catch((err) => setError(err instanceof ApiError ? err.message : "Could not load users."))
    listRoles(tenantId, accessToken)
      .then((r) => {
        setRoles(r)
        if (r.length && !r.some((x) => x.name === role)) setRole(r[0].name)
      })
      .catch(() => {})
  }

  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(reload, [tenantId, accessToken])

  async function handleInvite(e: React.FormEvent) {
    e.preventDefault()
    setError(null)
    setInviteNotice(null)
    setBusy(true)
    try {
      const result = await inviteUser(tenantId, email, role, accessToken)
      // emailed: the tenant's own configured mail relay delivered the credential
      // directly - see routes/tenants.py's _email_invite. Falls back to a shown
      // password only when no channel is configured or the send failed.
      setInviteNotice(
        result.emailed
          ? `Invited ${result.email} — sign-in details emailed to them directly.`
          : `Invited ${result.email}. Temporary password: ${result.temp_password} (shown once — share it securely; no email channel is configured for this tenant)`
      )
      setEmail("")
      reload()
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not invite this user.")
    } finally {
      setBusy(false)
    }
  }

  async function handleRoleChange(userId: string, newRole: string) {
    setError(null)
    try {
      await updateUserRole(tenantId, userId, newRole, accessToken)
      reload()
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not change this user's role.")
    }
  }

  async function handleRemove(userId: string) {
    setError(null)
    try {
      await removeUser(tenantId, userId, accessToken)
      reload()
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not remove this user.")
    }
  }

  const roleCounts = users?.reduce<Record<string, number>>((acc, u) => {
    acc[u.role] = (acc[u.role] ?? 0) + 1
    return acc
  }, {})
  const topRole = roleCounts && Object.entries(roleCounts).sort((a, b) => b[1] - a[1])[0]
  const q = query.trim().toLowerCase()
  const filteredUsers = q ? users?.filter((u) => u.email.toLowerCase().includes(q)) : users

  return (
    <div className="space-y-4">
      <PageHeader title="Users" description="Who can sign in to this tenant, and what they can do." />

      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        <Card className="py-3"><CardContent className="px-4"><StatCell label="Total users" value={users?.length ?? "—"} /></CardContent></Card>
        <Card className="py-3"><CardContent className="px-4"><StatCell label="Roles in use" value={roleCounts ? Object.keys(roleCounts).length : "—"} /></CardContent></Card>
        <Card className="py-3"><CardContent className="px-4"><StatCell label="Most common role" value={topRole ? roles.find((r) => r.name === topRole[0])?.label ?? topRole[0] : "—"} /></CardContent></Card>
        <Card className="py-3"><CardContent className="px-4"><StatCell label="Available roles" value={roles.length || "—"} /></CardContent></Card>
      </div>

      {error && (
        <Alert variant="destructive">
          <AlertCircle />
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      )}
      {inviteNotice && (
        <Alert>
          <Mail />
          <AlertDescription>{inviteNotice}</AlertDescription>
        </Alert>
      )}

      <div className="flex items-center justify-end">
        <div className="relative w-full max-w-[220px]">
          <Search className="pointer-events-none absolute top-1/2 left-2.5 size-3.5 -translate-y-1/2 text-muted-foreground" />
          <Input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Search email"
            className="h-8 pl-8 text-sm"
          />
        </div>
      </div>

      <div className="overflow-x-auto rounded-lg border border-border">
        <table className="w-full text-sm">
          <thead className="bg-muted text-left text-muted-foreground">
            <tr>
              <th className="px-3 py-1.5 font-medium">Email</th>
              <th className="px-3 py-1.5 font-medium">Role</th>
              <th className="px-3 py-1.5 font-medium">Added</th>
              <th className="px-3 py-1.5" />
            </tr>
          </thead>
          <tbody>
            {users === null && (
              <tr>
                <td colSpan={4} className="px-3 py-2.5 text-muted-foreground">
                  Loading…
                </td>
              </tr>
            )}
            {users?.length === 0 && (
              <tr>
                <td colSpan={4} className="px-3 py-2.5 text-muted-foreground">
                  —
                </td>
              </tr>
            )}
            {users && users.length > 0 && filteredUsers?.length === 0 && (
              <tr>
                <td colSpan={4} className="px-3 py-2.5 text-muted-foreground">
                  No users match "{query.trim()}".
                </td>
              </tr>
            )}
            {filteredUsers?.map((u) => (
              <tr key={u.id} className="border-t border-border">
                <td className="px-3 py-1.5">{u.email}</td>
                <td className="px-3 py-1.5">
                  <select
                    className="rounded-md border border-input bg-background px-2 py-1 text-sm focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none"
                    value={u.role}
                    onChange={(e) => handleRoleChange(u.id, e.target.value)}
                  >
                    {roles.map((r) => (
                      <option key={r.name} value={r.name}>
                        {r.label}
                      </option>
                    ))}
                    {!roles.some((r) => r.name === u.role) && <option value={u.role}>{u.role}</option>}
                  </select>
                </td>
                <td className="px-3 py-1.5 text-muted-foreground">{new Date(u.created_at).toLocaleDateString()}</td>
                <td className="px-3 py-1.5 text-right">
                  <AlertDialog>
                    <AlertDialogTrigger asChild>
                      <Button variant="ghost" size="icon" aria-label={`Remove ${u.email}`}>
                        <Trash2 className="size-4 text-destructive" />
                      </Button>
                    </AlertDialogTrigger>
                    <AlertDialogContent>
                      <AlertDialogHeader>
                        <AlertDialogTitle>Remove {u.email}?</AlertDialogTitle>
                        <AlertDialogDescription>
                          This user loses access to this tenant immediately. This cannot be undone from here.
                        </AlertDialogDescription>
                      </AlertDialogHeader>
                      <AlertDialogFooter>
                        <AlertDialogCancel>Cancel</AlertDialogCancel>
                        <AlertDialogAction onClick={() => handleRemove(u.id)}>Remove</AlertDialogAction>
                      </AlertDialogFooter>
                    </AlertDialogContent>
                  </AlertDialog>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <Card>
        <CardHeader>
          <CardTitle>Invite a user</CardTitle>
        </CardHeader>
        <CardContent>
          <form onSubmit={handleInvite} className="flex flex-wrap items-end gap-3">
            <div className="space-y-1.5">
              <Label htmlFor="inv-email">Email</Label>
              <Input id="inv-email" type="email" required value={email} onChange={(e) => setEmail(e.target.value)} />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="inv-role">Role</Label>
              <select
                id="inv-role"
                className="flex h-9 rounded-md border border-input bg-background px-3 py-1 text-sm shadow-sm focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 outline-none"
                value={role}
                onChange={(e) => setRole(e.target.value)}
              >
                {roles.map((r) => (
                  <option key={r.name} value={r.name}>
                    {r.label}
                  </option>
                ))}
              </select>
            </div>
            <Button type="submit" disabled={busy}>
              {busy ? "Inviting…" : "+ Invite"}
            </Button>
          </form>
        </CardContent>
      </Card>
    </div>
  )
}
