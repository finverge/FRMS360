import { useState } from "react"
import { AlertCircle, CheckCircle2 } from "lucide-react"

import { ApiError } from "@/api/client"
import { requestSandbox, type EntityType, type TenantOut } from "@/api/auth"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Alert, AlertDescription } from "@/components/ui/alert"

// Mirrors config_service/app/policy.py's ENTITY_TYPES exactly - see BR-109/BR-317 work
// on why this list is not guessed: which RBI Master Direction governs a tenant depends
// on this value being one the backend actually recognises.
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

export function SandboxSignupForm({ onCancel }: { onCancel: () => void }) {
  const [slug, setSlug] = useState("")
  const [legalName, setLegalName] = useState("")
  const [displayName, setDisplayName] = useState("")
  const [entityType, setEntityType] = useState<EntityType>("commercial_bank")
  const [adminEmail, setAdminEmail] = useState("")
  const [adminPassword, setAdminPassword] = useState("")
  const [error, setError] = useState<string | null>(null)
  const [created, setCreated] = useState<TenantOut | null>(null)
  const [busy, setBusy] = useState(false)

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    setError(null)
    setBusy(true)
    try {
      const tenant = await requestSandbox({
        slug,
        legal_name: legalName,
        display_name: displayName,
        entity_type: entityType,
        admin_email: adminEmail,
        admin_password: adminPassword,
      })
      setCreated(tenant)
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not create your sandbox.")
    } finally {
      setBusy(false)
    }
  }

  if (created) {
    return (
      <div className="space-y-4">
        <Alert variant="success">
          <CheckCircle2 />
          <AlertDescription>
            {created.display_name} is ready as a sandbox. It's fully functional for
            integration testing, and it can never submit traffic labelled as live — your
            Fraud360 contact promotes it to production when you're ready.
          </AlertDescription>
        </Alert>
        <Button type="button" className="w-full" onClick={onCancel}>
          Sign in
        </Button>
      </div>
    )
  }

  return (
    <div className="space-y-4">
      <p className="text-sm text-muted-foreground">
        A sandbox is fully functional for integration testing. It can never submit
        traffic labelled as live; your Fraud360 contact promotes it to production when
        you're ready.
      </p>
      <form onSubmit={handleSubmit} className="space-y-4" noValidate>
        <div className="space-y-2">
          <Label htmlFor="sb-slug">Organisation short name</Label>
          <Input
            id="sb-slug"
            placeholder="acme-bank"
            pattern="[a-z][a-z0-9-]{1,62}"
            required
            value={slug}
            onChange={(e) => setSlug(e.target.value)}
          />
        </div>
        <div className="space-y-2">
          <Label htmlFor="sb-legal">Legal name</Label>
          <Input id="sb-legal" required value={legalName} onChange={(e) => setLegalName(e.target.value)} />
        </div>
        <div className="space-y-2">
          <Label htmlFor="sb-display">Display name</Label>
          <Input id="sb-display" required value={displayName} onChange={(e) => setDisplayName(e.target.value)} />
        </div>
        <div className="space-y-2">
          <Label htmlFor="sb-entity">Entity type</Label>
          <select
            id="sb-entity"
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
        <div className="space-y-2">
          <Label htmlFor="sb-email">Your work email</Label>
          <Input
            id="sb-email"
            type="email"
            required
            value={adminEmail}
            onChange={(e) => setAdminEmail(e.target.value)}
          />
        </div>
        <div className="space-y-2">
          <Label htmlFor="sb-password">Choose a password</Label>
          <Input
            id="sb-password"
            type="password"
            minLength={8}
            required
            value={adminPassword}
            onChange={(e) => setAdminPassword(e.target.value)}
          />
        </div>

        {error && (
          <Alert variant="destructive">
            <AlertCircle />
            <AlertDescription>{error}</AlertDescription>
          </Alert>
        )}

        <div className="flex gap-2">
          <Button type="submit" className="flex-1" disabled={busy}>
            {busy ? "Creating…" : "Create sandbox"}
          </Button>
          <Button type="button" variant="ghost" onClick={onCancel}>
            Cancel
          </Button>
        </div>
      </form>
    </div>
  )
}
