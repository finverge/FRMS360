import { useState } from "react"

import { useSession } from "@/app/SessionContext"
import { ApiError } from "@/api/client"
import { createConfig, simulateBackfill, type ConfigCreate, type ConfigKind, type ConfigOut } from "@/api/configs"
import { Alert, AlertDescription } from "@/components/ui/alert"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { StructuredRuleEditor } from "./StructuredRuleEditor"
import {
  CONFIG_TEMPLATES,
  bodyToStructured,
  emptyStructuredRule,
  operativeThreshold,
  structuredToBody,
  type StructuredRule,
} from "./ruleBody"

const KINDS: ConfigKind[] = ["typology", "rule", "network_map", "policy"]

export function ConfigForm({
  tenantId,
  initial,
  onCreated,
  onCancel,
}: {
  tenantId: string
  // Present when duplicating an existing version (see DetectionConfigsPanel) - absent
  // for "+ New version", which starts from a blank kind picker instead.
  initial?: { kind: ConfigKind; name: string; version: string; body: Record<string, unknown> }
  onCreated: (config: ConfigOut) => void
  onCancel: () => void
}) {
  const { accessToken } = useSession()
  const [kind, setKind] = useState<ConfigKind>(initial?.kind ?? "typology")
  const [name, setName] = useState(initial?.name ?? "")
  const [version, setVersion] = useState(initial?.version ?? "1.0.0")
  const [mode, setMode] = useState<"structured" | "json">(
    initial && initial.kind === "rule" && (initial.body as any)?.config ? "structured" : "json"
  )
  const [jsonText, setJsonText] = useState(JSON.stringify(initial?.body ?? {}, null, 2))
  const [structured, setStructured] = useState<StructuredRule>(
    (initial?.kind === "rule" && bodyToStructured(initial.body)) || emptyStructuredRule()
  )
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  const [simDays, setSimDays] = useState("90")
  const [simBusy, setSimBusy] = useState(false)
  const [simError, setSimError] = useState<string | null>(null)
  const [simResult, setSimResult] = useState<{
    ruleId: string; threshold: number; days: number; transactions: number
    rows: { rule: string; actual: number; would: number }[]; mine: number; note: string
  } | null>(null)

  function currentBody(): Record<string, unknown> {
    return mode === "structured" ? structuredToBody(structured) : JSON.parse(jsonText)
  }

  function switchMode(next: "structured" | "json") {
    if (next === mode) return
    if (next === "json") {
      setJsonText(JSON.stringify(structuredToBody(structured), null, 2))
    } else {
      // Best-effort: if the JSON the operator pasted isn't a rule shape, fall back to a
      // blank structured form rather than erroring - matching the old console's
      // "Load a template to get started" placeholder for the same case.
      try {
        const parsed = JSON.parse(jsonText)
        setStructured(bodyToStructured(parsed) ?? emptyStructuredRule())
      } catch {
        setStructured(emptyStructuredRule())
      }
    }
    setMode(next)
  }

  function applyTemplate() {
    const tmpl = CONFIG_TEMPLATES[kind]
    setJsonText(JSON.stringify(tmpl, null, 2))
    if (kind === "rule") {
      setStructured(bodyToStructured(tmpl) ?? emptyStructuredRule())
      setMode("structured")
    } else {
      setMode("json")
    }
    const id = (tmpl as { id?: string }).id
    if (id && !name) setName(id.split("@")[0])
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    setError(null)
    let body: Record<string, unknown>
    try {
      body = currentBody()
    } catch (err) {
      setError(`Body is not valid JSON: ${err instanceof Error ? err.message : String(err)}`)
      return
    }
    setBusy(true)
    try {
      const payload: ConfigCreate = { kind, name, version, body }
      const created = await createConfig(tenantId, payload, accessToken)
      onCreated(created)
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not create this version.")
    } finally {
      setBusy(false)
    }
  }

  // BR-311: read-only. Nothing here writes a config - the operator still has to create
  // and activate a version above for a threshold to take effect.
  async function handleSimulate() {
    setSimError(null)
    setSimResult(null)
    let body: Record<string, unknown>
    try {
      body = currentBody()
    } catch (err) {
      setSimError(`Body is not valid JSON: ${err instanceof Error ? err.message : String(err)}`)
      return
    }
    const ruleId = String((body.id as string) || "").split("@")[0]
    const threshold = operativeThreshold(body)
    if (!ruleId || threshold === null) {
      setSimError(
        "This rule has no operative band with a threshold, so there is nothing to simulate. " +
          "Add a band marked operative."
      )
      return
    }
    const days = Number(simDays) || 90
    const to = new Date()
    const from = new Date(to.getTime() - days * 86400000)
    setSimBusy(true)
    try {
      const res = await simulateBackfill(
        tenantId,
        { window_from: from.toISOString(), window_to: to.toISOString(), thresholds: { [ruleId]: threshold } },
        accessToken
      )
      const would = res.would_fire || {}
      const actual = res.actual || {}
      const rules = Array.from(new Set([...Object.keys(would), ...Object.keys(actual)])).sort()
      setSimResult({
        ruleId,
        threshold,
        days,
        transactions: res.transactions || 0,
        rows: rules.map((r) => ({ rule: r, actual: actual[r] || 0, would: would[r] || 0 })),
        mine: (would[ruleId] || 0) - (actual[ruleId] || 0),
        note: res.note || "",
      })
    } catch (err) {
      setSimError(err instanceof ApiError ? err.message : "Could not run the simulation.")
    } finally {
      setSimBusy(false)
    }
  }

  return (
    <form onSubmit={handleSubmit} className="space-y-6 rounded-md border border-border p-4">
      <div className="grid gap-4 sm:grid-cols-4">
        <div className="space-y-2">
          <Label htmlFor="cf-kind">Kind</Label>
          <Select
            value={kind}
            onValueChange={(v) => {
              const nextKind = v as ConfigKind
              setKind(nextKind)
              // Structured editing only makes sense for a rule's parameters/exitConditions/
              // bands shape (see StructuredRuleEditor) - anything else falls through to
              // JSON, same as the old console did when it hit a non-rule body.
              if (nextKind !== "rule" && mode === "structured") setMode("json")
            }}
            disabled={!!initial}
          >
            <SelectTrigger id="cf-kind"><SelectValue /></SelectTrigger>
            <SelectContent>
              {KINDS.map((k) => (
                <SelectItem key={k} value={k}>{k}</SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
        <div className="space-y-2">
          <Label htmlFor="cf-name">Name</Label>
          <Input id="cf-name" placeholder="mule-layering" value={name} onChange={(e) => setName(e.target.value)} required />
        </div>
        <div className="space-y-2">
          <Label htmlFor="cf-version">Version</Label>
          <Input id="cf-version" placeholder="1.0.0" value={version} onChange={(e) => setVersion(e.target.value)} required />
        </div>
        <div className="flex items-end">
          <Button type="button" variant="outline" onClick={applyTemplate} className="w-full">
            Load template
          </Button>
        </div>
      </div>

      {kind === "rule" && (
        <div className="inline-flex h-9 w-fit items-center rounded-lg bg-muted p-1 text-sm text-muted-foreground">
          {(["structured", "json"] as const).map((m) => (
            <button
              key={m}
              type="button"
              onClick={() => switchMode(m)}
              className={
                "h-7 rounded-md px-3 font-medium capitalize transition-colors " +
                (mode === m ? "bg-background text-foreground shadow-sm" : "")
              }
            >
              {m}
            </button>
          ))}
        </div>
      )}

      {mode === "structured" ? (
        <StructuredRuleEditor
          value={structured}
          onChange={(next) => {
            setStructured(next)
            setJsonText(JSON.stringify(structuredToBody(next), null, 2))
          }}
        />
      ) : (
        <div className="space-y-2">
          <Label htmlFor="cf-body">Body (JSON)</Label>
          <textarea
            id="cf-body"
            rows={14}
            spellCheck={false}
            placeholder='{ "id": "…" }'
            value={jsonText}
            onChange={(e) => setJsonText(e.target.value)}
            className="w-full rounded-md border border-input bg-background px-3 py-2 font-mono text-xs shadow-sm focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 outline-none"
          />
        </div>
      )}

      {error && (
        <Alert variant="destructive">
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      )}

      <div className="flex flex-wrap items-center gap-3">
        <Button type="submit" disabled={busy}>{busy ? "Creating…" : "Create version"}</Button>
        {kind === "rule" && (
          <>
            <Button type="button" variant="outline" onClick={handleSimulate} disabled={simBusy}>
              {simBusy ? "Simulating…" : "Try against history"}
            </Button>
            <Label htmlFor="cf-sim-days" className="text-sm text-muted-foreground">over</Label>
            <Select value={simDays} onValueChange={setSimDays}>
              <SelectTrigger id="cf-sim-days" className="w-40"><SelectValue /></SelectTrigger>
              <SelectContent>
                <SelectItem value="30">last 30 days</SelectItem>
                <SelectItem value="60">last 60 days</SelectItem>
                <SelectItem value="90">last 90 days</SelectItem>
              </SelectContent>
            </Select>
          </>
        )}
        <Button type="button" variant="ghost" onClick={onCancel}>Cancel</Button>
      </div>

      {simError && (
        <Alert variant="destructive">
          <AlertDescription>{simError}</AlertDescription>
        </Alert>
      )}

      {simResult && (
        <div className="space-y-3 rounded-md border border-border p-4">
          <p className="text-sm">
            <b>{simResult.ruleId} at {simResult.threshold}</b> over {simResult.days} days ·{" "}
            {simResult.transactions.toLocaleString()} transactions scored
          </p>
          <p className={"text-sm font-medium " + (simResult.mine > 0 ? "text-destructive" : simResult.mine < 0 ? "text-success" : "text-muted-foreground")}>
            {simResult.mine === 0
              ? "No change to what this rule would have raised."
              : simResult.mine > 0
                ? `This band would have raised ${simResult.mine.toLocaleString()} more alert(s) on ${simResult.ruleId}.`
                : `This band would have raised ${Math.abs(simResult.mine).toLocaleString()} fewer alert(s) on ${simResult.ruleId}.`}
          </p>
          <div className="overflow-x-auto rounded-md border border-border">
            <table className="w-full text-sm">
              <thead className="bg-muted text-left text-muted-foreground">
                <tr>
                  <th className="px-3 py-2 font-medium">Rule</th>
                  <th className="px-3 py-2 text-right font-medium">Raised</th>
                  <th className="px-3 py-2 text-right font-medium">Would raise</th>
                  <th className="px-3 py-2 text-right font-medium">Change</th>
                </tr>
              </thead>
              <tbody>
                {simResult.rows.length === 0 && (
                  <tr><td colSpan={4} className="px-3 py-3 text-muted-foreground">Nothing fired either way.</td></tr>
                )}
                {simResult.rows.map((r) => {
                  const delta = r.would - r.actual
                  const sign = delta > 0 ? "+" : ""
                  return (
                    <tr key={r.rule} className="border-t border-border">
                      <td className="px-3 py-2 font-mono text-xs">{r.rule}</td>
                      <td className="px-3 py-2 text-right">{r.actual.toLocaleString()}</td>
                      <td className="px-3 py-2 text-right">{r.would.toLocaleString()}</td>
                      <td className={"px-3 py-2 text-right " + (delta > 0 ? "text-destructive" : delta < 0 ? "text-success" : "")}>
                        {sign}{delta.toLocaleString()}
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
          <p className="text-xs text-muted-foreground">
            {simResult.note} Nothing has been written — create a version above to make this band live.
          </p>
        </div>
      )}
    </form>
  )
}
