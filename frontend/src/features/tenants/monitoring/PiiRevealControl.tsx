import { useState } from "react"

import { Button } from "@/components/ui/button"
import { Badge } from "@/components/ui/badge"

// Mirrors the three-condition gate every reveal-capable endpoint enforces server-side
// (_resolve_reveal): the role must hold can_reveal_pii, the caller must ask explicitly,
// and a justification of at least 8 characters is required. This control only ever
// *asks* - the server is the actual enforcement point, and a role without the
// capability gets a 403 back (surfaced as the usual error state) rather than silently
// masked data, so there is nothing to hide behind this control failing softly.
export function PiiRevealControl({
  onApply,
  disabled,
}: {
  onApply: (justification: string) => void
  disabled?: boolean
}) {
  const [open, setOpen] = useState(false)
  const [justification, setJustification] = useState("")

  if (!open) {
    return (
      <Button type="button" size="sm" variant="outline" onClick={() => setOpen(true)} disabled={disabled}>
        Reveal PII
      </Button>
    )
  }

  const tooShort = justification.trim().length < 8

  return (
    <div className="flex flex-wrap items-center gap-2 rounded-md border border-border p-2">
      <Badge variant="outline">audited</Badge>
      <input
        type="text"
        placeholder="Justification (min 8 characters) — this view is audited"
        value={justification}
        onChange={(e) => setJustification(e.target.value)}
        className="h-8 w-72 rounded-md border border-input bg-background px-2 text-sm shadow-sm focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 outline-none"
      />
      <Button
        type="button"
        size="sm"
        disabled={tooShort}
        onClick={() => {
          onApply(justification.trim())
          setOpen(false)
        }}
      >
        Apply
      </Button>
      <Button type="button" size="sm" variant="ghost" onClick={() => setOpen(false)}>Cancel</Button>
    </div>
  )
}
