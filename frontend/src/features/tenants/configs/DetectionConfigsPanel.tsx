import { useEffect, useState } from "react"
import { AlertCircle, Search } from "lucide-react"

import { useSession } from "@/app/SessionContext"
import { ApiError } from "@/api/client"
import {
  activateConfig,
  listConfigs,
  rejectActivation,
  type ConfigKind,
  type ConfigOut,
} from "@/api/configs"
import { Alert, AlertDescription } from "@/components/ui/alert"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card, CardContent } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { PageHeader, StatCell } from "@/components/ui/sidebar"
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { ConfigForm } from "./ConfigForm"
import { bumpVersion, LANE_NOTES, laneOf, type Lane } from "./ruleBody"

const STATUS_VARIANT: Record<string, "default" | "secondary" | "destructive" | "success" | "outline"> = {
  draft: "secondary",
  pending_activation: "default",
  active: "success",
  archived: "outline",
}

const LANES: { value: "all" | Lane; label: string }[] = [
  { value: "all", label: "All" },
  { value: "a", label: "Lane A — Real-Time" },
  { value: "b", label: "Lane B — Near-Real-Time" },
  { value: "c", label: "Lane C — Periodic" },
]

export function DetectionConfigsPanel({ tenantId }: { tenantId: string }) {
  const { accessToken } = useSession()
  const [configs, setConfigs] = useState<ConfigOut[] | null>(null)
  const [lane, setLane] = useState<"all" | Lane>("all")
  const [query, setQuery] = useState("")
  const [error, setError] = useState<string | null>(null)
  const [busyId, setBusyId] = useState<string | null>(null)
  const [viewing, setViewing] = useState<ConfigOut | null>(null)
  const [formOpen, setFormOpen] = useState(false)
  const [duplicateFrom, setDuplicateFrom] = useState<
    { kind: ConfigKind; name: string; version: string; body: Record<string, unknown> } | undefined
  >(undefined)

  function reload() {
    listConfigs(tenantId, accessToken)
      .then(setConfigs)
      .catch((err) => setError(err instanceof ApiError ? err.message : "Could not load detection configs."))
  }

  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(reload, [tenantId, accessToken])

  function openNew() {
    setDuplicateFrom(undefined)
    setFormOpen(true)
    setViewing(null)
  }

  function openDuplicate(cfg: ConfigOut) {
    setDuplicateFrom({ kind: cfg.kind as ConfigKind, name: cfg.name, version: bumpVersion(cfg.version), body: cfg.body })
    setFormOpen(true)
    setViewing(null)
  }

  // BR-715: the same endpoint proposes on a draft and confirms on a pending proposal -
  // the button label below already tells the operator which phase they're in, so the
  // handler itself doesn't need to branch on it.
  async function handleActivate(cfg: ConfigOut) {
    setError(null)
    setBusyId(cfg.id)
    try {
      await activateConfig(tenantId, cfg.id, accessToken)
      reload()
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not activate this version.")
    } finally {
      setBusyId(null)
    }
  }

  async function handleReject(cfg: ConfigOut) {
    setError(null)
    setBusyId(cfg.id)
    try {
      await rejectActivation(tenantId, cfg.id, accessToken)
      reload()
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not reject this proposal.")
    } finally {
      setBusyId(null)
    }
  }

  const q = query.trim().toLowerCase()
  const filtered = (configs ?? [])
    .filter((c) => lane === "all" || laneOf(c) === lane)
    .filter((c) => !q || c.name.toLowerCase().includes(q) || c.kind.toLowerCase().includes(q) || c.version.toLowerCase().includes(q))
  const activeCount = configs?.filter((c) => c.status === "active").length
  const pendingCount = configs?.filter((c) => c.status === "pending_activation").length
  const draftCount = configs?.filter((c) => c.status === "draft").length

  return (
    <div className="space-y-4">
      <PageHeader
        title="Detection configs"
        description="Tazama rule / typology / network-map versions"
        action={!formOpen && <Button size="sm" onClick={openNew}>+ New version</Button>}
      />

      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        <Card className="py-3"><CardContent className="px-4"><StatCell label="Total configs" value={configs?.length ?? "—"} /></CardContent></Card>
        <Card className="py-3"><CardContent className="px-4"><StatCell label="Active" value={activeCount ?? "—"} tone="success" /></CardContent></Card>
        <Card className="py-3"><CardContent className="px-4"><StatCell label="Pending confirm" value={pendingCount ?? "—"} tone={pendingCount ? "warning" : "default"} /></CardContent></Card>
        <Card className="py-3"><CardContent className="px-4"><StatCell label="Drafts" value={draftCount ?? "—"} /></CardContent></Card>
      </div>

      {/* Lane grouping: which decisioning lane a rule belongs to. Lane A is the inline
          set decision-service actually evaluates in the payment window
          (services/decision_service/app/inline_rules.py); Lane B is everything else the
          near-real-time engine scores. Lane C ("Periodic Borrower Assessment") has no
          dedicated engine yet - the QUAL-* rules shown here are its closest built
          analogue. typology/network_map/policy configs are not rule-family-specific, so
          they only appear under "All". */}
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="inline-flex h-9 w-fit items-center rounded-lg bg-muted p-1 text-sm text-muted-foreground">
          {LANES.map((l) => (
            <button
              key={l.value}
              type="button"
              onClick={() => setLane(l.value)}
              className={
                "h-7 rounded-md px-3 font-medium whitespace-nowrap transition-colors " +
                (lane === l.value ? "bg-background text-foreground shadow-sm" : "")
              }
            >
              {l.label}
            </button>
          ))}
        </div>
        <div className="relative w-full max-w-[220px]">
          <Search className="pointer-events-none absolute top-1/2 left-2.5 size-3.5 -translate-y-1/2 text-muted-foreground" />
          <Input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Search name, kind or version"
            className="h-8 pl-8 text-sm"
          />
        </div>
      </div>
      {lane !== "all" && <p className="text-sm text-muted-foreground">{LANE_NOTES[lane]}</p>}

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
              <th className="px-3 py-1.5 font-medium">Kind</th>
              <th className="px-3 py-1.5 font-medium">Name</th>
              <th className="px-3 py-1.5 font-medium">Version</th>
              <th className="px-3 py-1.5 font-medium">Status</th>
              <th className="px-3 py-1.5" />
            </tr>
          </thead>
          <tbody>
            {configs === null && (
              <tr><td colSpan={5} className="px-3 py-2.5 text-muted-foreground">Loading…</td></tr>
            )}
            {configs?.length === 0 && (
              <tr><td colSpan={5} className="px-3 py-2.5 text-muted-foreground">No configs</td></tr>
            )}
            {configs && configs.length > 0 && filtered.length === 0 && (
              <tr><td colSpan={5} className="px-3 py-2.5 text-muted-foreground">No configs match this lane and search.</td></tr>
            )}
            {filtered.map((c) => (
              <tr key={c.id} className="border-t border-border align-top">
                <td className="px-3 py-1.5">{c.kind}</td>
                <td className="px-3 py-1.5">{c.name}</td>
                <td className="px-3 py-1.5 font-mono text-xs">{c.version}</td>
                <td className="px-3 py-1.5">
                  <Badge
                    variant={STATUS_VARIANT[c.status] ?? "outline"}
                    title={
                      c.status === "pending_activation" && c.proposed_by
                        ? `Proposed by ${c.proposed_by} (${c.proposed_by_role}) - a different tenant_admin, risk_manager or platform_admin must confirm.`
                        : undefined
                    }
                  >
                    {c.status.replace("_", " ")}
                  </Badge>
                </td>
                <td className="px-3 py-1.5 text-right">
                  <div className="flex flex-wrap justify-end gap-x-3 gap-y-1">
                    <button type="button" className="text-sm text-primary hover:underline" onClick={() => { setViewing(c); setFormOpen(false) }}>
                      View
                    </button>
                    {c.status === "draft" && (
                      <button
                        type="button"
                        className="text-sm text-primary hover:underline disabled:opacity-50"
                        disabled={busyId === c.id}
                        onClick={() => handleActivate(c)}
                      >
                        Propose activation
                      </button>
                    )}
                    {c.status === "pending_activation" && (
                      <>
                        <button
                          type="button"
                          className="text-sm text-primary hover:underline disabled:opacity-50"
                          disabled={busyId === c.id}
                          onClick={() => handleActivate(c)}
                        >
                          Confirm
                        </button>
                        <button
                          type="button"
                          className="text-sm text-destructive hover:underline disabled:opacity-50"
                          disabled={busyId === c.id}
                          onClick={() => handleReject(c)}
                        >
                          Reject
                        </button>
                      </>
                    )}
                    <button type="button" className="text-sm text-primary hover:underline" onClick={() => openDuplicate(c)}>
                      Duplicate
                    </button>
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {formOpen && (
        <ConfigForm
          tenantId={tenantId}
          initial={duplicateFrom}
          onCreated={() => { setFormOpen(false); reload() }}
          onCancel={() => setFormOpen(false)}
        />
      )}

      <Dialog open={!!viewing} onOpenChange={(open) => !open && setViewing(null)}>
        <DialogContent className="max-w-2xl">
          <DialogHeader>
            <DialogTitle>{viewing?.kind} · {viewing?.name} · {viewing?.version}</DialogTitle>
          </DialogHeader>
          <pre className="max-h-[60vh] overflow-auto rounded-md bg-muted p-4 text-xs">
            {viewing ? JSON.stringify(viewing.body, null, 2) : ""}
          </pre>
        </DialogContent>
      </Dialog>
    </div>
  )
}
