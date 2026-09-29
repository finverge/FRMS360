import { useState } from "react"

import { lookupSso, ssoStartUrl, type SsoProvider } from "@/api/auth"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"

/** Org-short-name -> "which IdPs does this bank offer" -> full-page redirect into one.
 * Public by necessity (services/tenant_service/app/routes/sso.py's providers() route
 * docstring: "the console needs it before anyone has authenticated"). */
export function SsoLookup() {
  const [org, setOrg] = useState("")
  const [providers, setProviders] = useState<SsoProvider[] | null>(null)
  const [notFound, setNotFound] = useState(false)
  const [busy, setBusy] = useState(false)

  async function find() {
    if (!org.trim()) return
    setBusy(true)
    setNotFound(false)
    setProviders(null)
    try {
      // Public route always answers 200, even for an unknown tenant slug - an empty
      // providers array, not a 404 (routes/sso.py's providers() docstring: this is
      // reachable before anyone has authenticated, and must not leak which slugs exist
      // via a different status code).
      const result = await lookupSso(org.trim().toLowerCase())
      if (result.providers.length === 0) setNotFound(true)
      else setProviders(result.providers)
    } catch {
      setNotFound(true)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="space-y-2">
      <Label htmlFor="sso-org" className="sr-only">
        Your organisation's short name
      </Label>
      <div className="flex gap-2">
        <Input
          id="sso-org"
          placeholder="your organisation's short name"
          value={org}
          onChange={(e) => setOrg(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && find()}
        />
        <Button type="button" variant="outline" size="sm" onClick={find} disabled={busy}>
          Find sign-in
        </Button>
      </div>
      {notFound && (
        <p className="text-sm text-muted-foreground" role="status">
          No federated sign-in found for "{org}" — use your platform password above.
        </p>
      )}
      {providers && providers.length > 0 && (
        <div className="flex flex-col gap-2 pt-1">
          {providers.map((p) => (
            <Button key={p.slug} variant="outline" asChild>
              <a href={ssoStartUrl(org.trim().toLowerCase(), p.slug)}>
                Continue with {p.display_name}
              </a>
            </Button>
          ))}
        </div>
      )}
    </div>
  )
}
