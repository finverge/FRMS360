import { useState } from "react"

import { useSession } from "@/app/SessionContext"
import { ApiError } from "@/api/client"
import { createRole, updateRole, type RoleOut } from "@/api/tenants"
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
import { TENANT_DASHBOARDS, TENANT_MODULES } from "./roleCatalogue"

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
  // renaming isn't supported server-side (see roles.py's module docstring on why).
  role?: RoleOut
  open: boolean
  onOpenChange: (open: boolean) => void
  onSaved: (role: RoleOut) => void
}) {
  const { accessToken } = useSession()
  const [name, setName] = useState(role?.name ?? "")
  const [label, setLabel] = useState(role?.label ?? "")
  const [modules, setModules] = useState<string[]>(role?.modules.map((m) => m.key) ?? [])
  const [dashboards, setDashboards] = useState<string[]>(role?.dashboards.map((d) => d.key) ?? [])
  const [canAdmin, setCanAdmin] = useState(role?.can_admin_tenant ?? false)
  const [canPii, setCanPii] = useState(role?.can_reveal_pii ?? false)
  const [canActivate, setCanActivate] = useState(role?.can_activate_config ?? false)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  const isEdit = !!role
  // A change that RAISES one of these three from its current value is staged as a
  // maker-checker elevation rather than applied immediately (roles.py's update_role) -
  // flagged here so the operator isn't surprised when "Save" doesn't take effect at once.
  const raisesCapability =
    isEdit &&
    ((canAdmin && !role!.can_admin_tenant) ||
      (canPii && !role!.can_reveal_pii) ||
      (canActivate && !role!.can_activate_config))

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    setError(null)
    setBusy(true)
    try {
      const payload = {
        label,
        modules,
        dashboards,
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
      <DialogContent className="max-w-2xl">
        <DialogHeader>
          <DialogTitle>{isEdit ? `Edit ${role!.label}` : "New role"}</DialogTitle>
          <DialogDescription>
            {isEdit
              ? "Lowering a capability, or changing modules/dashboards/label, applies immediately. Raising a capability needs a different administrator to confirm before it takes effect."
              : "Beyond the platform's fixed ten - guardrails refuse platform-only modules or dashboards regardless of who is asking."}
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
            <Label>Modules</Label>
            <div className="flex flex-wrap gap-4">
              {TENANT_MODULES.map((m) => (
                <label key={m.key} className="flex items-center gap-2 text-sm" title={m.description}>
                  <input
                    type="checkbox"
                    checked={modules.includes(m.key)}
                    onChange={(e) => setModules(toggled(modules, m.key, e.target.checked))}
                  />
                  {m.label}
                </label>
              ))}
            </div>
          </div>

          <div className="space-y-2">
            <Label>Dashboards <span className="text-muted-foreground font-normal">(within Monitoring)</span></Label>
            <div className="grid grid-cols-2 gap-2 sm:grid-cols-3">
              {TENANT_DASHBOARDS.map((d) => (
                <label key={d.key} className="flex items-center gap-2 text-sm" title={d.description}>
                  <input
                    type="checkbox"
                    checked={dashboards.includes(d.key)}
                    onChange={(e) => setDashboards(toggled(dashboards, d.key, e.target.checked))}
                  />
                  {d.label}
                </label>
              ))}
            </div>
          </div>

          <div className="space-y-2">
            <Label>Capabilities</Label>
            <div className="flex flex-wrap gap-4">
              <label className="flex items-center gap-2 text-sm" title="May change branding/users/configs for its tenant">
                <input type="checkbox" checked={canAdmin} onChange={(e) => setCanAdmin(e.target.checked)} />
                Admin tenant
              </label>
              <label className="flex items-center gap-2 text-sm" title="May unmask customer PII (account numbers, device ids, IPs) with a justification">
                <input type="checkbox" checked={canPii} onChange={(e) => setCanPii(e.target.checked)} />
                Reveal PII
              </label>
              <label className="flex items-center gap-2 text-sm" title="May propose or confirm a detection-config activation (BR-715)">
                <input type="checkbox" checked={canActivate} onChange={(e) => setCanActivate(e.target.checked)} />
                Activate configs
              </label>
            </div>
          </div>

          {raisesCapability && (
            <Alert>
              <AlertDescription>
                This raises a capability this role doesn't already have — saving will stage it for
                confirmation by a different administrator rather than applying it immediately.
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
            <Button type="submit" disabled={busy}>{busy ? "Saving…" : "Save"}</Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  )
}
