import { Badge, badgeVariants } from "@/components/ui/badge"
import type { VariantProps } from "class-variance-authority"

type BadgeVariant = VariantProps<typeof badgeVariants>["variant"]

const SEVERITY_VARIANT: Record<string, BadgeVariant> = {
  critical: "critical",
  high: "high",
  medium: "medium",
  low: "low",
}

// Same critical/high/medium/low palette as the FilterBar's severity chips, applied
// as a solid badge so a severity value reads the same color wherever it shows up -
// filter, row table, or (later) evidence drawer.
export function SeverityBadge({ value }: { value: unknown }) {
  const s = String(value ?? "").toLowerCase()
  const variant = SEVERITY_VARIANT[s]
  if (!variant) return <span className="text-sm capitalize">{s || "—"}</span>
  return (
    <Badge variant={variant} className="capitalize">
      {s}
    </Badge>
  )
}

const DISPOSITION_VARIANT: Record<string, BadgeVariant> = {
  true_positive: "success",
  false_positive: "secondary",
  pending: "warning",
}

export function DispositionBadge({ value }: { value: unknown }) {
  const d = String(value ?? "")
  const variant = DISPOSITION_VARIANT[d]
  const label = d.replace(/_/g, " ")
  if (!variant) return <span className="text-sm capitalize">{label || "—"}</span>
  return (
    <Badge variant={variant} className="capitalize">
      {label}
    </Badge>
  )
}

const DECISION_VARIANT: Record<string, BadgeVariant> = {
  ALLOW: "success",
  HOLD: "warning",
  BLOCK: "destructive",
}

// AI Insights' recommended decision (see AiInsightsPanel) - same badge language as
// DispositionBadge above, a different vocabulary since this is a model's suggestion,
// not the analyst's recorded disposition.
export function DecisionBadge({ value }: { value: unknown }) {
  const d = String(value ?? "").toUpperCase()
  const variant = DECISION_VARIANT[d]
  if (!variant) return <span className="text-sm">{d || "—"}</span>
  return <Badge variant={variant}>{d}</Badge>
}
