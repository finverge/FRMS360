import { useEffect, useMemo, useState } from "react"

import { useSession } from "@/app/SessionContext"
import { ApiError } from "@/api/client"
import { createRole, getPermissionCatalogue, updateRole, type PermissionCatalogue, type RoleOut } from "@/api/tenants"
import { Alert, AlertDescription } from "@/components/ui/alert"
import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"

function toggled(list: string[], key: string, on: boolean): string[] {
  return on ? [...list, key] : list.filter((k) => k !== key)
}

export function RoleForm({
  tenantId,
  role,
  open,
  onOpenChange,
  onSaved,
}: {
  tenantId: string
  // Present in edit mode; absent for "+ New role". Name is fixed once created -
  // renaming isn't supported server-side (it is the stable key users, tokens and the audit
  // trail refer to).
  role?: RoleOut
  open: boolean
  onOpenChange: (open: boolean) => void
  onSaved: (role: RoleOut) => void
}) {
  const { accessToken } = useSession()
  // What can be granted comes from the server (the actions, modules and dashboards that exist),
  // so a new gated action appears here without a console release.
  const [catalogue, setCatalogue] = useState<PermissionCatalogue | null>(null)
  const [name, setName] = useState(role?.name ?? "")
  const [label, setLabel] = useState(role?.label ?? "")
  const [description, setDescription] = useState(role?.description ?? "")
  const [modules, setModules] = useState<string[]>(role?.modules.map((m) => m.key) ?? [])
  const [dashboards, setDashboards] = useState<string[]>(role?.dashboards.map((d) => d.key) ?? [])
  const [permissions, setPermissions] = useState<string[]>(role?.permissions ?? [])
  const [canAdmin, setCanAdmin] = useState(role?.can_admin_tenant ?? false)
  const [canPii, setCanPii] = useState(role?.can_reveal_pii ?? false)
  const [canActivate, setCanActivate] = useState(role?.can_activate_config ?? false)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    getPermissionCatalogue(tenantId, accessToken)
      .then(setCatalogue)
      .catch((err) => setError(err instanceof ApiError ? err.message : "Could not load what can be granted."))
  }, [tenantId, accessToken])

  const groups = useMemo(() => {
    const out: Record<string, PermissionCatalogue["permissions"]> = {}
    for (const p of catalogue?.permissions ?? []) (out[p.group] ??= []).push(p)
    return Object.entries(out)
  }, [catalogue])

  const isEdit = !!role
  // A change that RAISES a capability, or adds an action, is staged for a second person
  // rather than applied at once (roles.py's update_role) - flagged here so "Save" not taking
  // effect immediately is not a surprise.
  const raises =
    isEdit &&
    ((canAdmin && !role!.can_admin_tenant) ||
      (canPii && !role!.can_reveal_pii) ||
      (canActivate && !role!.can_activate_config) ||
      permissions.some((p) => !role!.permissions.includes(p)))

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    setError(null)
    setBusy(true)
    try {
      const payload = {
        label,
        description,
        modules,
        dashboards,
        permissions,
        can_admin_tenant: canAdmin,
        can_reveal_pii: canPii,
        can_activate_config: canActivate,
      }
      const saved = isEdit
        ? await updateRole(tenantId, role!.name, payload, accessToken)
        : await createRole(tenantId, { ...payload, name }, accessToken)
      onSaved(saved)
      onOpenChange(false)
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not save this role.")
    } finally {
      setBusy(false)
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[90vh] max-w-3xl overflow-y-auto">
        <DialogHeader>
          <DialogTitle>{isEdit ? `Edit ${role!.label}` : "New role"}</DialogTitle>
          <DialogDescription>
            {isEdit
              ? "Taking something away, or changing the label, description, modules or dashboards, applies immediately. Giving a role more (a capability or an action) needs a different administrator to confirm before it takes effect."
              : "A role is only what you grant it here. Platform-only modules and dashboards are refused whoever asks."}
          </DialogDescription>
        </DialogHeader>

        <form onSubmit={handleSubmit} className="space-y-5">
          <div className="grid gap-4 sm:grid-cols-2">
            <div className="space-y-2">
              <Label htmlFor="rf-name">Name</Label>
              <Input
                id="rf-name"
                placeholder="regional_fraud_lead"
                value={name}
                onChange={(e) => setName(e.target.value)}
                disabled={isEdit}
                required
                pattern="^[a-z][a-z0-9_]{1,31}$"
                title="lowercase letters, digits and underscores, starting with a letter"
              />
            </div>
            <div className="space-y-2">
              <Label htmlFor="rf-label">Label</Label>
              <Input id="rf-label" placeholder="Regional Fraud Lead" value={label} onChange={(e) => setLabel(e.target.value)} required />
            </div>
          </div>

          <div className="space-y-2">
            <Label htmlFor="rf-desc">Description <span className="font-normal text-muted-foreground">(shown to people in this role on their home page)</span></Label>
            <Input id="rf-desc" maxLength={600} placeholder="What this role is for" value={description} onChange={(e) => setDescription(e.target.value)} />
          </div>

          <div className="space-y-2">
            <Label>Modules</Label>
            <div className="flex flex-wrap gap-4">
              {catalogue?.modules.map((m) => (
                <label key={m.key} className="flex items-center gap-2 text-sm" title={m.description}>
                  <input type="checkbox" checked={modules.includes(m.key)} onChange={(e) => setModules(toggled(modules, m.key, e.target.checked))} />
                  {m.label}
                </label>
              ))}
            </div>
          </div>

          <div className="space-y-2">
            <Label>Dashboards <span className="font-normal text-muted-foreground">(within Monitoring; the first one in the role's list is its home dashboard; they keep the order they were ticked in)</span></Label>
            <div className="grid grid-cols-2 gap-2 sm:grid-cols-3">
              {catalogue?.dashboards.map((d) => (
                <label key={d.key} className="flex items-center gap-2 text-sm" title={`${d.persona} - ${d.question}`}>
                  <input type="checkbox" checked={dashboards.includes(d.key)} onChange={(e) => setDashboards(toggled(dashboards, d.key, e.target.checked))} />
                  {d.label}
                </label>
              ))}
            </div>
          </div>

          <div className="space-y-3">
            <Label>Actions this role may perform</Label>
            {groups.map(([group, items]) => (
              <fieldset key={group} className="rounded-md border border-border p-3">
                <legend className="px-1 text-xs font-medium tracking-wide text-muted-foreground uppercase">{group}</legend>
                <div className="grid gap-1.5 sm:grid-cols-2">
                  {items.map((p) => (
                    <label key={p.key} className="flex items-center gap-2 text-sm">
                      <input type="checkbox" checked={permissions.includes(p.key)} onChange={(e) => setPermissions(toggled(permissions, p.key, e.target.checked))} />
                      {p.label}
                    </label>
                  ))}
                </div>
              </fieldset>
            ))}
          </div>

          <div className="space-y-2">
            <Label>Capabilities</Label>
            <div className="flex flex-wrap gap-4">
              <label className="flex items-center gap-2 text-sm" title="May change branding, users, roles and configuration for its tenant">
                <input type="checkbox" checked={canAdmin} onChange={(e) => setCanAdmin(e.target.checked)} />
                Administer the tenant
              </label>
              <label className="flex items-center gap-2 text-sm" title="May unmask customer identifiers (account numbers, device ids, IPs) with a justification">
                <input type="checkbox" checked={canPii} onChange={(e) => setCanPii(e.target.checked)} />
                Reveal customer data
              </label>
              <label className="flex items-center gap-2 text-sm" title="May propose or confirm a detection-config activation (BR-715)">
                <input type="checkbox" checked={canActivate} onChange={(e) => setCanActivate(e.target.checked)} />
                Activate configurations
              </label>
            </div>
          </div>

          {raises && (
            <Alert>
              <AlertDescription>
                This gives the role something it does not have now. Saving stages it for confirmation by a
                different administrator; anything you are taking away applies at once.
              </AlertDescription>
            </Alert>
          )}

          {error && (
            <Alert variant="destructive">
              <AlertDescription>{error}</AlertDescription>
            </Alert>
          )}

          <DialogFooter>
            <Button type="button" variant="ghost" onClick={() => onOpenChange(false)}>Cancel</Button>
            <Button type="submit" disabled={busy || !catalogue}>{busy ? "Saving…" : "Save"}</Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  )
}
