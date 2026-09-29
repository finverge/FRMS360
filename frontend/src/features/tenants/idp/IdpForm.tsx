import { useState } from "react"
import { Trash2 } from "lucide-react"

import { useSession } from "@/app/SessionContext"
import { ApiError } from "@/api/client"
import { createProvider, updateProvider, type IdpOut } from "@/api/identityProviders"
import { Alert, AlertDescription } from "@/components/ui/alert"
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
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { ASSIGNABLE_ROLE_OPTIONS } from "../roles/roleCatalogue"

export function IdpForm({
  tenantId,
  provider,
  open,
  onOpenChange,
  onSaved,
}: {
  tenantId: string
  provider?: IdpOut
  open: boolean
  onOpenChange: (open: boolean) => void
  onSaved: (provider: IdpOut) => void
}) {
  const { accessToken } = useSession()
  const isEdit = !!provider

  const [slug, setSlug] = useState(provider?.slug ?? "")
  const [displayName, setDisplayName] = useState(provider?.display_name ?? "")
  const [protocol, setProtocol] = useState<"oidc" | "saml">(provider?.protocol ?? "oidc")
  const [enabled, setEnabled] = useState(provider?.enabled ?? true)
  const [issuer, setIssuer] = useState(provider?.issuer ?? "")
  const [clientId, setClientId] = useState(provider?.client_id ?? "")
  const [clientSecret, setClientSecret] = useState("")
  const [discoveryUrl, setDiscoveryUrl] = useState(provider?.discovery_url ?? "")
  const [authorizeUrl, setAuthorizeUrl] = useState(provider?.authorize_url ?? "")
  const [tokenUrl, setTokenUrl] = useState(provider?.token_url ?? "")
  const [jwksUrl, setJwksUrl] = useState(provider?.jwks_url ?? "")
  const [scopes, setScopes] = useState(provider?.scopes ?? "openid email profile")
  const [emailClaim, setEmailClaim] = useState(provider?.email_claim ?? "email")
  const [groupsClaim, setGroupsClaim] = useState(provider?.groups_claim ?? "groups")
  const [samlSsoUrl, setSamlSsoUrl] = useState(provider?.saml_sso_url ?? "")
  const [samlCertificate, setSamlCertificate] = useState("")
  const [samlEmailAttr, setSamlEmailAttr] = useState(provider?.saml_email_attribute ?? "")
  const [samlGroupsAttr, setSamlGroupsAttr] = useState(provider?.saml_groups_attribute ?? "")
  const [roleRows, setRoleRows] = useState<{ group: string; role: string }[]>(
    Object.entries(provider?.role_mapping ?? {}).map(([group, role]) => ({ group, role }))
  )
  const [defaultRole, setDefaultRole] = useState(provider?.default_role ?? "analyst")
  const [jitProvisioning, setJitProvisioning] = useState(provider?.jit_provisioning ?? true)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    setError(null)
    setBusy(true)
    const role_mapping: Record<string, string> = {}
    for (const row of roleRows) {
      if (row.group.trim()) role_mapping[row.group.trim()] = row.role
    }
    const payload = {
      slug, display_name: displayName, protocol, enabled, issuer, client_id: clientId,
      client_secret: clientSecret, discovery_url: discoveryUrl, authorize_url: authorizeUrl,
      token_url: tokenUrl, jwks_url: jwksUrl, scopes, saml_sso_url: samlSsoUrl,
      saml_certificate: samlCertificate, saml_email_attribute: samlEmailAttr,
      saml_groups_attribute: samlGroupsAttr, email_claim: emailClaim, groups_claim: groupsClaim,
      role_mapping, default_role: defaultRole, jit_provisioning: jitProvisioning,
    }
    try {
      const saved = isEdit
        ? await updateProvider(tenantId, provider!.id, payload, accessToken)
        : await createProvider(tenantId, payload, accessToken)
      onSaved(saved)
      onOpenChange(false)
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not save this identity provider.")
    } finally {
      setBusy(false)
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-2xl">
        <DialogHeader>
          <DialogTitle>{isEdit ? `Edit ${provider!.display_name}` : "Add identity provider"}</DialogTitle>
        </DialogHeader>

        <form onSubmit={handleSubmit} className="max-h-[70vh] space-y-5 overflow-y-auto pr-1">
          <div className="grid gap-4 sm:grid-cols-2">
            <div className="space-y-2">
              <Label htmlFor="idp-slug">Slug</Label>
              <Input id="idp-slug" placeholder="entra-corp" value={slug} onChange={(e) => setSlug(e.target.value)} required />
            </div>
            <div className="space-y-2">
              <Label htmlFor="idp-display">Display name</Label>
              <Input id="idp-display" placeholder="Corporate Entra ID" value={displayName} onChange={(e) => setDisplayName(e.target.value)} required />
            </div>
            <div className="space-y-2">
              <Label htmlFor="idp-protocol">Protocol</Label>
              <Select value={protocol} onValueChange={(v) => setProtocol(v as "oidc" | "saml")}>
                <SelectTrigger id="idp-protocol"><SelectValue /></SelectTrigger>
                <SelectContent>
                  <SelectItem value="oidc">OIDC</SelectItem>
                  <SelectItem value="saml">SAML 2.0</SelectItem>
                </SelectContent>
              </Select>
            </div>
            <label className="flex items-center gap-2 self-end pb-2 text-sm">
              <input type="checkbox" checked={enabled} onChange={(e) => setEnabled(e.target.checked)} />
              Enabled
            </label>
          </div>

          <div className="space-y-2">
            <Label htmlFor="idp-issuer">Issuer</Label>
            <Input id="idp-issuer" value={issuer} onChange={(e) => setIssuer(e.target.value)} />
          </div>

          {protocol === "oidc" ? (
            <div className="space-y-4 rounded-md border border-border p-4">
              <h5 className="text-sm font-medium">OIDC</h5>
              <div className="grid gap-4 sm:grid-cols-2">
                <div className="space-y-2">
                  <Label htmlFor="idp-client-id">Client ID</Label>
                  <Input id="idp-client-id" value={clientId} onChange={(e) => setClientId(e.target.value)} />
                </div>
                <div className="space-y-2">
                  <Label htmlFor="idp-client-secret">Client secret</Label>
                  <Input
                    id="idp-client-secret"
                    type="password"
                    value={clientSecret}
                    onChange={(e) => setClientSecret(e.target.value)}
                    placeholder={isEdit && provider!.has_client_secret ? "unchanged" : ""}
                  />
                </div>
                <div className="space-y-2 sm:col-span-2">
                  <Label htmlFor="idp-discovery">Discovery URL</Label>
                  <Input
                    id="idp-discovery"
                    placeholder="https://.../.well-known/openid-configuration"
                    value={discoveryUrl}
                    onChange={(e) => setDiscoveryUrl(e.target.value)}
                  />
                </div>
                <div className="space-y-2">
                  <Label htmlFor="idp-authorize-url">Authorize URL <span className="text-muted-foreground font-normal">(if no discovery URL)</span></Label>
                  <Input id="idp-authorize-url" value={authorizeUrl} onChange={(e) => setAuthorizeUrl(e.target.value)} />
                </div>
                <div className="space-y-2">
                  <Label htmlFor="idp-token-url">Token URL</Label>
                  <Input id="idp-token-url" value={tokenUrl} onChange={(e) => setTokenUrl(e.target.value)} />
                </div>
                <div className="space-y-2">
                  <Label htmlFor="idp-jwks-url">JWKS URL</Label>
                  <Input id="idp-jwks-url" value={jwksUrl} onChange={(e) => setJwksUrl(e.target.value)} />
                </div>
                <div className="space-y-2">
                  <Label htmlFor="idp-scopes">Scopes</Label>
                  <Input id="idp-scopes" value={scopes} onChange={(e) => setScopes(e.target.value)} />
                </div>
                <div className="space-y-2">
                  <Label htmlFor="idp-email-claim">Email claim</Label>
                  <Input id="idp-email-claim" value={emailClaim} onChange={(e) => setEmailClaim(e.target.value)} />
                </div>
                <div className="space-y-2">
                  <Label htmlFor="idp-groups-claim">Groups claim</Label>
                  <Input id="idp-groups-claim" value={groupsClaim} onChange={(e) => setGroupsClaim(e.target.value)} />
                </div>
              </div>
            </div>
          ) : (
            <div className="space-y-4 rounded-md border border-border p-4">
              <h5 className="text-sm font-medium">SAML 2.0</h5>
              <div className="grid gap-4 sm:grid-cols-2">
                <div className="space-y-2 sm:col-span-2">
                  <Label htmlFor="idp-saml-sso-url">SSO URL</Label>
                  <Input id="idp-saml-sso-url" value={samlSsoUrl} onChange={(e) => setSamlSsoUrl(e.target.value)} />
                </div>
                <div className="space-y-2 sm:col-span-2">
                  <Label htmlFor="idp-saml-cert">Certificate (PEM)</Label>
                  <textarea
                    id="idp-saml-cert"
                    rows={4}
                    spellCheck={false}
                    value={samlCertificate}
                    onChange={(e) => setSamlCertificate(e.target.value)}
                    placeholder={isEdit && provider!.has_saml_certificate ? "unchanged" : "-----BEGIN CERTIFICATE-----"}
                    className="w-full rounded-md border border-input bg-background px-3 py-2 font-mono text-xs shadow-sm focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 outline-none"
                  />
                </div>
                <div className="space-y-2">
                  <Label htmlFor="idp-saml-email-attr">Email attribute</Label>
                  <Input id="idp-saml-email-attr" value={samlEmailAttr} onChange={(e) => setSamlEmailAttr(e.target.value)} />
                </div>
                <div className="space-y-2">
                  <Label htmlFor="idp-saml-groups-attr">Groups attribute</Label>
                  <Input id="idp-saml-groups-attr" value={samlGroupsAttr} onChange={(e) => setSamlGroupsAttr(e.target.value)} />
                </div>
              </div>
            </div>
          )}

          <div className="space-y-3">
            <div className="flex items-center justify-between">
              <Label>Directory group → role mapping</Label>
              <Button type="button" variant="outline" size="sm" onClick={() => setRoleRows([...roleRows, { group: "", role: "analyst" }])}>
                + Mapping
              </Button>
            </div>
            {roleRows.length === 0 && <p className="text-sm text-muted-foreground">None — every sign-in gets the default role below.</p>}
            {roleRows.map((row, i) => (
              <div key={i} className="grid grid-cols-[1fr_12rem_2rem] items-center gap-2">
                <Input
                  placeholder="Directory group, e.g. FRAUD-ANALYSTS"
                  value={row.group}
                  onChange={(e) => {
                    const next = [...roleRows]
                    next[i] = { ...row, group: e.target.value }
                    setRoleRows(next)
                  }}
                />
                <Select
                  value={row.role}
                  onValueChange={(v) => {
                    const next = [...roleRows]
                    next[i] = { ...row, role: v }
                    setRoleRows(next)
                  }}
                >
                  <SelectTrigger><SelectValue /></SelectTrigger>
                  <SelectContent>
                    {ASSIGNABLE_ROLE_OPTIONS.map((r) => (
                      <SelectItem key={r.key} value={r.key}>{r.label}</SelectItem>
                    ))}
                  </SelectContent>
                </Select>
                <Button type="button" variant="ghost" size="icon" aria-label="Remove mapping" onClick={() => setRoleRows(roleRows.filter((_, j) => j !== i))}>
                  <Trash2 className="size-4 text-destructive" />
                </Button>
              </div>
            ))}
          </div>

          <div className="grid gap-4 sm:grid-cols-2">
            <div className="space-y-2">
              <Label htmlFor="idp-default-role">Default role <span className="text-muted-foreground font-normal">(no group matched)</span></Label>
              <Select value={defaultRole} onValueChange={setDefaultRole}>
                <SelectTrigger id="idp-default-role"><SelectValue /></SelectTrigger>
                <SelectContent>
                  {ASSIGNABLE_ROLE_OPTIONS.map((r) => (
                    <SelectItem key={r.key} value={r.key}>{r.label}</SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <label className="flex items-center gap-2 self-end pb-2 text-sm" title="Create a user on first sign-in instead of requiring a prior invite">
              <input type="checkbox" checked={jitProvisioning} onChange={(e) => setJitProvisioning(e.target.checked)} />
              Just-in-time provisioning
            </label>
          </div>

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
