import { Search } from "lucide-react"
import { useState } from "react"

import { useSession } from "@/app/SessionContext"
import { ApiError } from "@/api/client"
import { screenOfac, type OfacScreenOut } from "@/api/ofac"
import { Alert, AlertDescription } from "@/components/ui/alert"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { PageHeader } from "@/components/ui/sidebar"

// Investigator-driven sanctions screening against the real OFAC SDN list (see
// services/analytics_service/app/ofac.py and scripts/load_ofac_sdn.py) - not per-
// transaction automatic screening, because Fraud360's fact tables carry account
// numbers, not customer/counterparty names. An investigator types in a name they've
// obtained elsewhere (a case note, a KYC document, a call transcript) and screens it.
export function SanctionsScreeningPanel({ tenantId }: { tenantId: string }) {
  const { accessToken } = useSession()
  const [name, setName] = useState("")
  const [busy, setBusy] = useState(false)
  const [result, setResult] = useState<OfacScreenOut | null>(null)
  const [error, setError] = useState<string | null>(null)

  function handleScreen() {
    const trimmed = name.trim()
    if (!trimmed) return
    setBusy(true)
    setError(null)
    screenOfac(tenantId, trimmed, accessToken)
      .then(setResult)
      .catch((err) => setError(err instanceof ApiError ? err.message : "Could not screen this name."))
      .finally(() => setBusy(false))
  }

  return (
    <div className="space-y-4">
      <PageHeader
        title="Sanctions Screening"
        description="Fuzzy-match a name against the real OFAC Specially Designated Nationals (SDN) list."
      />

      <div className="flex flex-wrap items-center gap-2">
        <div className="relative w-full max-w-md">
          <Search className="pointer-events-none absolute top-1/2 left-2.5 size-3.5 -translate-y-1/2 text-muted-foreground" />
          <Input
            value={name}
            onChange={(e) => setName(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && handleScreen()}
            placeholder="Full name to screen (e.g. from a case note or KYC document)"
            className="pl-8"
          />
        </div>
        <Button onClick={handleScreen} disabled={busy || !name.trim()}>
          {busy ? "Screening…" : "Screen"}
        </Button>
      </div>

      {error && (
        <Alert variant="destructive">
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      )}

      {result && (
        <div className="space-y-2">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <h6 className="text-sm font-medium">
              Results for "{result.query}"{" "}
              <span className="text-muted-foreground font-normal">
                ({result.matches.length} match{result.matches.length === 1 ? "" : "es"} of {result.list_size.toLocaleString("en-IN")} SDN entries)
              </span>
            </h6>
            {result.list_refreshed_at && (
              <span className="text-xs text-muted-foreground">
                List last refreshed {new Date(result.list_refreshed_at).toLocaleDateString("en-IN")}
              </span>
            )}
          </div>

          {result.matches.length === 0 ? (
            <p className="text-sm text-muted-foreground">No sanctions-list matches found for this name.</p>
          ) : (
            <div className="overflow-x-auto rounded-lg border border-border">
              <table className="w-full text-sm">
                <thead className="bg-muted text-left text-muted-foreground">
                  <tr>
                    <th className="px-3 py-1.5 font-medium">SDN Name</th>
                    <th className="px-3 py-1.5 font-medium">Type</th>
                    <th className="px-3 py-1.5 font-medium">Program</th>
                    <th className="px-3 py-1.5 font-medium">Matched on</th>
                    <th className="px-3 py-1.5 text-right font-medium">Similarity</th>
                  </tr>
                </thead>
                <tbody>
                  {result.matches.map((m) => (
                    <tr key={m.ent_num} className="border-t border-border align-top">
                      <td className="px-3 py-1.5 font-medium">{m.name}</td>
                      <td className="px-3 py-1.5 capitalize text-muted-foreground">{m.type || "entity"}</td>
                      <td className="px-3 py-1.5">
                        <Badge variant="outline">{m.program || "—"}</Badge>
                      </td>
                      <td className="px-3 py-1.5">
                        {m.matched_via === "alias" ? (
                          <span>
                            alias <span className="font-medium">"{m.matched_on}"</span>
                          </span>
                        ) : (
                          <span className="text-muted-foreground">primary name</span>
                        )}
                        {m.remarks && <div className="mt-0.5 text-xs text-muted-foreground">{m.remarks}</div>}
                      </td>
                      <td className="px-3 py-1.5 text-right font-mono text-xs">{(m.score * 100).toFixed(0)}%</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}

          <p className="text-xs text-muted-foreground">
            OFAC's own real, public SDN list (US Treasury) - fuzzy-matched, so review every result yourself
            rather than treating a low-similarity row as a confirmed hit. This is a name-matching tool, not
            a compliance decision.
          </p>
        </div>
      )}
    </div>
  )
}
