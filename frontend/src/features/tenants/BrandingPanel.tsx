import { useEffect, useState } from "react"
import { AlertCircle, CheckCircle2 } from "lucide-react"

import { useSession } from "@/app/SessionContext"
import { ApiError } from "@/api/client"
import { getBranding, putBranding, type BrandingOut, type BrandingUpsert } from "@/api/branding"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Alert, AlertDescription } from "@/components/ui/alert"
import { PageHeader } from "@/components/ui/sidebar"

export function BrandingPanel({ tenantId }: { tenantId: string }) {
  const { accessToken } = useSession()
  const [branding, setBranding] = useState<BrandingOut | null>(null)
  const [form, setForm] = useState<BrandingUpsert>({})
  const [error, setError] = useState<string | null>(null)
  const [saved, setSaved] = useState(false)
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    setBranding(null)
    setError(null)
    getBranding(tenantId, accessToken)
      .then((b) => {
        setBranding(b)
        setForm(b)
      })
      .catch((err) => {
        // 404 just means branding was never provisioned (shouldn't happen post-onboard,
        // but the API models it as a real "not found" rather than defaulting silently) -
        // fall back to an empty editable form rather than blocking the page.
        if (err instanceof ApiError && err.status === 404) setForm({})
        else setError(err instanceof ApiError ? err.message : "Could not load branding.")
      })
  }, [tenantId, accessToken])

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    setError(null)
    setSaved(false)
    setBusy(true)
    try {
      const updated = await putBranding(tenantId, form, accessToken)
      setBranding(updated)
      setSaved(true)
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not save branding.")
    } finally {
      setBusy(false)
    }
  }

  function field<K extends keyof BrandingUpsert>(key: K, value: BrandingUpsert[K]) {
    setForm((f) => ({ ...f, [key]: value }))
    setSaved(false)
  }

  return (
    <div className="space-y-4">
      <PageHeader title="Branding" description="Logo, colors and theme this tenant's users see." />
      <div className="grid gap-4 md:grid-cols-2">
      <Card>
      <CardHeader><CardTitle>Appearance</CardTitle></CardHeader>
      <CardContent>
      <form onSubmit={handleSubmit} className="space-y-3" noValidate>
        <div className="space-y-1.5">
          <Label htmlFor="br-display">Display name</Label>
          <Input
            id="br-display"
            value={form.display_name ?? ""}
            onChange={(e) => field("display_name", e.target.value)}
          />
        </div>
        <div className="space-y-1.5">
          <Label htmlFor="br-logo">Logo URL</Label>
          <Input id="br-logo" value={form.logo_url ?? ""} onChange={(e) => field("logo_url", e.target.value)} />
        </div>
        <div className="grid grid-cols-3 gap-3">
          <div className="space-y-1.5">
            <Label htmlFor="br-primary">Primary</Label>
            <Input
              id="br-primary"
              type="color"
              className="h-9 p-1"
              value={form.primary_color ?? "#0F6E7A"}
              onChange={(e) => field("primary_color", e.target.value)}
            />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="br-accent">Accent</Label>
            <Input
              id="br-accent"
              type="color"
              className="h-9 p-1"
              value={form.accent_color ?? "#19A6B8"}
              onChange={(e) => field("accent_color", e.target.value)}
            />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="br-neutral">Neutral</Label>
            <Input
              id="br-neutral"
              type="color"
              className="h-9 p-1"
              value={form.neutral_color ?? "#5A6472"}
              onChange={(e) => field("neutral_color", e.target.value)}
            />
          </div>
        </div>
        <div className="space-y-1.5">
          <Label htmlFor="br-theme">Default theme</Label>
          <select
            id="br-theme"
            className="flex h-9 w-full rounded-md border border-input bg-background px-3 py-1 text-sm shadow-sm focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 outline-none"
            value={form.default_theme ?? "light"}
            onChange={(e) => field("default_theme", e.target.value as "light" | "dark")}
          >
            <option value="light">Light</option>
            <option value="dark">Dark</option>
          </select>
        </div>
        <div className="space-y-1.5">
          <Label htmlFor="br-domain">Custom domain</Label>
          <Input
            id="br-domain"
            value={form.custom_domain ?? ""}
            onChange={(e) => field("custom_domain", e.target.value)}
          />
        </div>

        {error && (
          <Alert variant="destructive">
            <AlertCircle />
            <AlertDescription>{error}</AlertDescription>
          </Alert>
        )}
        {saved && (
          <Alert variant="success">
            <CheckCircle2 />
            <AlertDescription>Saved.</AlertDescription>
          </Alert>
        )}

        <Button type="submit" disabled={busy || branding === null && form.display_name === undefined}>
          {busy ? "Saving…" : "Save branding"}
        </Button>
      </form>
      </CardContent>
      </Card>

      <Card>
      <CardHeader><CardTitle>Live preview</CardTitle></CardHeader>
      <CardContent>
        <div
          className="flex flex-col items-start gap-2.5 rounded-lg border border-border p-4"
          style={{ backgroundColor: form.neutral_color ? `${form.neutral_color}11` : undefined }}
        >
          <div
            className="flex size-10 items-center justify-center rounded-md text-lg font-bold text-white"
            style={{ backgroundColor: form.primary_color ?? "#0F6E7A" }}
          >
            ◆
          </div>
          <p className="font-semibold">{form.display_name || "Tenant"}</p>
          <Button
            type="button"
            className="pointer-events-none"
            style={{ backgroundColor: form.primary_color ?? "#0F6E7A" }}
          >
            Primary action
          </Button>
          <span
            className="rounded px-2 py-1 text-xs font-medium text-white"
            style={{ backgroundColor: form.accent_color ?? "#19A6B8" }}
          >
            Accent
          </span>
        </div>
      </CardContent>
      </Card>
      </div>
    </div>
  )
}
