import { Trash2 } from "lucide-react"

import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import type { StructuredRule } from "./ruleBody"

/** Form-only editor for a rule config's id/desc/parameters/exitConditions/bands - the
 * shape config_service actually evaluates (see ews_catalogue.py's rule_body()). Anything
 * outside this shape (family, comparator, qualitative, observedUnit - server-computed
 * metadata on catalogue rules) still has to go through JSON mode; this covers the part
 * an admin actually tunes day to day (BR-311 threshold changes). */
export function StructuredRuleEditor({
  value,
  onChange,
}: {
  value: StructuredRule
  onChange: (next: StructuredRule) => void
}) {
  function set<K extends keyof StructuredRule>(key: K, v: StructuredRule[K]) {
    onChange({ ...value, [key]: v })
  }

  return (
    <div className="space-y-6">
      <div className="grid gap-4 sm:grid-cols-2">
        <div className="space-y-2">
          <Label htmlFor="rule-id">ID</Label>
          <Input id="rule-id" placeholder="018@1.0.0" value={value.id} onChange={(e) => set("id", e.target.value)} />
        </div>
        <div className="space-y-2">
          <Label htmlFor="rule-desc">Description</Label>
          <Input id="rule-desc" value={value.desc} onChange={(e) => set("desc", e.target.value)} />
        </div>
      </div>

      <div className="space-y-3">
        <div className="flex items-center justify-between">
          <h5 className="text-sm font-medium">Parameters</h5>
          <Button
            type="button"
            variant="outline"
            size="sm"
            onClick={() => set("parameters", [...value.parameters, { name: "", value: "", type: "number" }])}
          >
            + Parameter
          </Button>
        </div>
        {value.parameters.length === 0 && <p className="text-sm text-muted-foreground">None yet.</p>}
        {value.parameters.map((p, i) => (
          <div key={i} className="grid grid-cols-[1fr_1fr_7rem_2rem] items-center gap-2">
            <Input
              placeholder="Name"
              value={p.name}
              onChange={(e) => {
                const next = [...value.parameters]
                next[i] = { ...p, name: e.target.value }
                set("parameters", next)
              }}
            />
            <Input
              placeholder="Value"
              type={p.type === "number" ? "number" : "text"}
              value={p.value}
              onChange={(e) => {
                const next = [...value.parameters]
                next[i] = { ...p, value: e.target.value }
                set("parameters", next)
              }}
            />
            <Select
              value={p.type}
              onValueChange={(v) => {
                const next = [...value.parameters]
                next[i] = { ...p, type: v as "number" | "string" }
                set("parameters", next)
              }}
            >
              <SelectTrigger><SelectValue /></SelectTrigger>
              <SelectContent>
                <SelectItem value="number">number</SelectItem>
                <SelectItem value="string">string</SelectItem>
              </SelectContent>
            </Select>
            <Button
              type="button"
              variant="ghost"
              size="icon"
              aria-label="Remove parameter"
              onClick={() => set("parameters", value.parameters.filter((_, j) => j !== i))}
            >
              <Trash2 className="size-4 text-destructive" />
            </Button>
          </div>
        ))}
      </div>

      <div className="space-y-3">
        <div className="flex items-center justify-between">
          <h5 className="text-sm font-medium">Exit conditions</h5>
          <Button
            type="button"
            variant="outline"
            size="sm"
            onClick={() => set("exitConditions", [...value.exitConditions, { ref: "", reason: "" }])}
          >
            + Exit condition
          </Button>
        </div>
        {value.exitConditions.length === 0 && <p className="text-sm text-muted-foreground">None yet.</p>}
        {value.exitConditions.map((ex, i) => (
          <div key={i} className="grid grid-cols-[7rem_1fr_2rem] items-center gap-2">
            <Input
              placeholder=".x00"
              value={ex.ref}
              onChange={(e) => {
                const next = [...value.exitConditions]
                next[i] = { ...ex, ref: e.target.value }
                set("exitConditions", next)
              }}
            />
            <Input
              placeholder="Reason"
              value={ex.reason}
              onChange={(e) => {
                const next = [...value.exitConditions]
                next[i] = { ...ex, reason: e.target.value }
                set("exitConditions", next)
              }}
            />
            <Button
              type="button"
              variant="ghost"
              size="icon"
              aria-label="Remove exit condition"
              onClick={() => set("exitConditions", value.exitConditions.filter((_, j) => j !== i))}
            >
              <Trash2 className="size-4 text-destructive" />
            </Button>
          </div>
        ))}
      </div>

      <div className="space-y-3">
        <div className="flex items-center justify-between">
          <h5 className="text-sm font-medium">Bands</h5>
          <Button
            type="button"
            variant="outline"
            size="sm"
            onClick={() =>
              set("bands", [...value.bands, { ref: "", lower: "", upper: "", reason: "", operative: false }])
            }
          >
            + Band
          </Button>
        </div>
        {value.bands.length === 0 && <p className="text-sm text-muted-foreground">None yet.</p>}
        {value.bands.map((b, i) => (
          <div key={i} className="grid grid-cols-[6rem_6rem_6rem_1fr_5.5rem_2rem] items-center gap-2">
            <Input
              placeholder=".01"
              value={b.ref}
              onChange={(e) => {
                const next = [...value.bands]
                next[i] = { ...b, ref: e.target.value }
                set("bands", next)
              }}
            />
            <Input
              placeholder="Lower"
              type="number"
              value={b.lower}
              onChange={(e) => {
                const next = [...value.bands]
                next[i] = { ...b, lower: e.target.value }
                set("bands", next)
              }}
            />
            <Input
              placeholder="Upper"
              type="number"
              value={b.upper}
              onChange={(e) => {
                const next = [...value.bands]
                next[i] = { ...b, upper: e.target.value }
                set("bands", next)
              }}
            />
            <Input
              placeholder="Reason"
              value={b.reason}
              onChange={(e) => {
                const next = [...value.bands]
                next[i] = { ...b, reason: e.target.value }
                set("bands", next)
              }}
            />
            <label className="flex items-center gap-1.5 text-sm text-muted-foreground" title="Try against history (BR-311) varies this band's threshold - only one band should carry it.">
              <input
                type="checkbox"
                checked={b.operative}
                onChange={(e) => {
                  // Exactly one operative band per rule - config_service reads the first
                  // one it finds (routes/configs.py:internal_active_rules), so a second
                  // checked box would silently be ignored rather than layered.
                  const next = value.bands.map((row, j) => ({ ...row, operative: j === i && e.target.checked }))
                  set("bands", next)
                }}
              />
              Operative
            </label>
            <Button
              type="button"
              variant="ghost"
              size="icon"
              aria-label="Remove band"
              onClick={() => set("bands", value.bands.filter((_, j) => j !== i))}
            >
              <Trash2 className="size-4 text-destructive" />
            </Button>
          </div>
        ))}
      </div>
    </div>
  )
}
