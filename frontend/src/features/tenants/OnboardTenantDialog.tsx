import { useState } from "react"
import { AlertCircle } from "lucide-react"

import { useSession } from "@/app/SessionContext"
import { ApiError } from "@/api/client"
import { createTenant, type TenantOut } from "@/api/tenants"
import type { EntityType } from "@/api/auth"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Alert, AlertDescription } from "@/components/ui/alert"
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
  DialogFooter,
} from "@/components/ui/dialog"

// Same closed set as config_service/app/policy.py's ENTITY_TYPES - see
// SandboxSignupForm.tsx for why this is not guessed.
const ENTITY_TYPES: { value: EntityType; label: string }[] = [
  { value: "commercial_bank", label: "Commercial Bank" },
  { value: "aifi", label: "All India Financial Institution (AIFI)" },
  { value: "urban_cooperative", label: "Urban Co-operative Bank (UCB)" },
  { value: "state_cooperative", label: "State Co-operative Bank (StCB)" },
  { value: "central_cooperative", label: "Central / District Co-operative Bank (CCB)" },
  { value: "rrb", label: "Regional Rural Bank" },
  { value: "local_area_bank", label: "Local Area Bank (LAB)" },
  { value: "small_finance_bank", label: "Small Finance Bank (SFB)" },
  { value: "payments_bank", label: "Payments Bank (PB)" },
  { value: "nbfc", label: "NBFC" },
  { value: "hfc", label: "Housing Finance Company" },
]

export function OnboardTenantDialog({
  open,
  onOpenChange,
  onCreated,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  onCreated: (tenant: TenantOut) => void
}) {
  const { accessToken } = useSession()
  const [slug, setSlug] = useState("")
  const [legalName, setLegalName] = useState("")
  const [displayName, setDisplayName] = useState("")
  const [entityType, setEntityType] = useState<EntityType>("commercial_bank")
  const [adminEmail, setAdminEmail] = useState("")
  const [adminPassword, setAdminPassword] = useState("")
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  function reset() {
    setSlug("")
    setLegalName("")
    setDisplayName("")
    setEntityType("commercial_bank")
    setAdminEmail("")
    setAdminPassword("")
    setError(null)
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    setError(null)
    setBusy(true)
    try {
      const tenant = await createTenant(
        {
          slug,
          legal_name: legalName,
          display_name: displayName,
          entity_type: entityType,
          admin_email: adminEmail,
          admin_password: adminPassword,
        },
        accessToken
      )
      onCreated(tenant)
      onOpenChange(false)
      reset()
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not onboard this tenant.")
    } finally {
      setBusy(false)
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Onboard a tenant</DialogTitle>
          <DialogDescription>
            Provisions the tenant, its first admin, default branding and default detection
            configs - the same orchestration BR-109's self-service sandbox signup runs, as
            a production (non-sandbox) tenant.
          </DialogDescription>
        </DialogHeader>
        <form onSubmit={handleSubmit} className="space-y-4" noValidate>
          <div className="grid grid-cols-2 gap-4">
            <div className="space-y-2">
              <Label htmlFor="ot-slug">Slug</Label>
              <Input id="ot-slug" placeholder="hdfc-bank" required value={slug} onChange={(e) => setSlug(e.target.value)} />
            </div>
            <div className="space-y-2">
              <Label htmlFor="ot-entity">Entity type</Label>
              <select
                id="ot-entity"
                className="flex h-9 w-full rounded-md border border-input bg-background px-3 py-1 text-sm shadow-sm focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 outline-none"
                value={entityType}
                onChange={(e) => setEntityType(e.target.value as EntityType)}
              >
                {ENTITY_TYPES.map((t) => (
                  <option key={t.value} value={t.value}>
                    {t.label}
                  </option>
                ))}
              </select>
            </div>
          </div>
          <div className="space-y-2">
            <Label htmlFor="ot-legal">Legal name</Label>
            <Input id="ot-legal" required value={legalName} onChange={(e) => setLegalName(e.target.value)} />
          </div>
          <div className="space-y-2">
            <Label htmlFor="ot-display">Display name</Label>
            <Input id="ot-display" required value={displayName} onChange={(e) => setDisplayName(e.target.value)} />
          </div>
          <div className="grid grid-cols-2 gap-4">
            <div className="space-y-2">
              <Label htmlFor="ot-admin-email">Admin email</Label>
              <Input
                id="ot-admin-email"
                type="email"
                required
                value={adminEmail}
                onChange={(e) => setAdminEmail(e.target.value)}
              />
            </div>
            <div className="space-y-2">
              <Label htmlFor="ot-admin-password">Admin password</Label>
              <Input
                id="ot-admin-password"
                type="password"
                minLength={8}
                required
                value={adminPassword}
                onChange={(e) => setAdminPassword(e.target.value)}
              />
            </div>
          </div>

          {error && (
            <Alert variant="destructive">
              <AlertCircle />
              <AlertDescription>{error}</AlertDescription>
            </Alert>
          )}

          <DialogFooter>
            <Button type="button" variant="ghost" onClick={() => onOpenChange(false)}>
              Cancel
            </Button>
            <Button type="submit" disabled={busy}>
              {busy ? "Provisioning…" : "Provision tenant"}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  )
}
