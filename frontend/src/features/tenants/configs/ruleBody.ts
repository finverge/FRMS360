// Shared helpers for the detection-config editor: rule-body shape, starter templates,
// lane classification, and the version/threshold arithmetic the old vanilla-JS console
// used (services/gateway/app/static/app.js) - ported rather than redesigned, since the
// underlying config_service contract hasn't changed.
import type { ConfigKind, ConfigOut } from "@/api/configs"

export interface Param {
  name: string
  value: string
  type: "number" | "string"
}

export interface ExitCondition {
  ref: string
  reason: string
}

export interface Band {
  ref: string
  lower: string
  upper: string
  reason: string
  operative: boolean
}

export interface StructuredRule {
  id: string
  desc: string
  parameters: Param[]
  exitConditions: ExitCondition[]
  bands: Band[]
}

export function emptyStructuredRule(): StructuredRule {
  return { id: "", desc: "", parameters: [], exitConditions: [], bands: [] }
}

/** Rule JSON body -> structured editor state. Returns null if the body isn't a rule
 * shape (no `config` key) - the caller falls back to JSON-only editing in that case. */
export function bodyToStructured(body: Record<string, unknown> | null | undefined): StructuredRule | null {
  const cfg = body?.config as Record<string, unknown> | undefined
  if (!body || !cfg) return null
  const parameters = (Array.isArray(cfg.parameters) ? cfg.parameters : []).map((p: any) => ({
    name: p.ParameterName ?? "",
    value: p.ParameterValue === undefined || p.ParameterValue === null ? "" : String(p.ParameterValue),
    type: p.ParameterType === "string" ? "string" : "number",
  })) as Param[]
  const exitConditions = (Array.isArray(cfg.exitConditions) ? cfg.exitConditions : []).map((e: any) => ({
    ref: e.subRuleRef ?? "",
    reason: e.reason ?? "",
  })) as ExitCondition[]
  const bands = (Array.isArray(cfg.bands) ? cfg.bands : []).map((b: any) => ({
    ref: b.subRuleRef ?? "",
    lower: b.lowerLimit === undefined || b.lowerLimit === null ? "" : String(b.lowerLimit),
    upper: b.upperLimit === undefined || b.upperLimit === null ? "" : String(b.upperLimit),
    reason: b.reason ?? "",
    operative: !!b.operative,
  })) as Band[]
  return { id: String(body.id ?? ""), desc: String(body.desc ?? ""), parameters, exitConditions, bands }
}

/** Structured editor state -> rule JSON body (config_service's ConfigCreate.body shape). */
export function structuredToBody(s: StructuredRule): Record<string, unknown> {
  return {
    id: s.id || "rule@1.0.0",
    cfg: "1.0.0",
    desc: s.desc,
    config: {
      parameters: s.parameters.map((p) => ({
        ParameterName: p.name,
        ParameterValue: p.type === "number" ? Number(p.value) || 0 : p.value,
        ParameterType: p.type,
      })),
      exitConditions: s.exitConditions.map((e) => ({ subRuleRef: e.ref, reason: e.reason })),
      bands: s.bands.map((b) => {
        const band: Record<string, unknown> = { subRuleRef: b.ref, reason: b.reason }
        if (b.lower !== "" && !Number.isNaN(Number(b.lower))) band.lowerLimit = Number(b.lower)
        if (b.upper !== "" && !Number.isNaN(Number(b.upper))) band.upperLimit = Number(b.upper)
        if (b.operative) band.operative = true
        return band
      }),
    },
  }
}

// Starter templates so the editor isn't a blank box - one per kind offered in the form.
export const CONFIG_TEMPLATES: Record<ConfigKind, Record<string, unknown>> = {
  rule: {
    id: "018@1.0.0", cfg: "1.0.0", desc: "Pass-through drain velocity",
    config: {
      parameters: [{ ParameterName: "lookbackMs", ParameterValue: 600000, ParameterType: "number" }],
      exitConditions: [{ subRuleRef: ".x00", reason: "No qualifying inflow in window" }],
      bands: [
        { subRuleRef: ".01", upperLimit: 0.5, reason: "Outflow < 50% of recent inflow" },
        { subRuleRef: ".03", lowerLimit: 0.9, reason: "Outflow >= 90% (pass-through)", operative: true },
      ],
    },
  },
  typology: {
    id: "mule-layering@1.0.0", cfg: "1.0.0", desc: "Mule-account layering typology (LAY family)",
    rules: [{ id: "018@1.0.0", cfg: "1.0.0", termId: "t018",
      wghts: [{ ref: ".x00", wght: 0 }, { ref: ".02", wght: 50 }, { ref: ".03", wght: 150 }] }],
    expression: "t018 + t024 + t030 + t044",
    workflow: { alertThreshold: 150, interdictionThreshold: 300 },
  },
  network_map: {
    active: true, cfg: "1.0.0",
    messages: [{ id: "004@1.0.0", cfg: "1.0.0", txTp: "pacs.008.001.10",
      typologies: [{ id: "mule-layering@1.0.0", cfg: "1.0.0", rules: [{ id: "018@1.0.0", cfg: "1.0.0" }] }] }],
  },
  // Shape matches config_service/app/policy.py:policy_body() exactly, seeded with the
  // commercial_bank defaults, so an admin edits real numbers rather than inventing the
  // schema from scratch. Activation refuses this without a completed "attestation" block
  // (BR-104) - that gate lives server-side, not in this editor.
  policy: {
    entity_type: "commercial_bank",
    entity_label: "Commercial Bank",
    governing_direction: "Fraud Risk Management in Commercial Banks, 2026 (RBI/DoS/2026-27/412)",
    reporting_to: ["RBI", "FIU-IND"],
    ucb_tier: null,
    principal_officer_name: "",
    thresholds: {
      natural_justice_days: 21, show_cause_within_days: 7,
      fmr_filing_days: 7, str_filing_days: 7, staff_accountability_days: 180,
      ews_examination_days: 30,
      lea_referral_paise: 1_000_000_00 * 10, board_reporting_paise: 1_000_000_00 * 10,
      material_fraud_paise: 100_000_00 * 10,
      severity_critical_score: 380, severity_high_score: 260, severity_medium_score: 150,
      sla_breach_hours: 24, stalled_case_days: 30,
      sla_hours_by_severity: { critical: 4, high: 12, medium: 24, low: 72 },
      retain_sessions_days: 90, retain_notifications_days: 180,
      retain_raw_ingest_days: 400, retain_transactions_days: 1825,
      retain_alerts_days: 730, retain_closed_cases_days: 1825,
      retain_fraud_records_days: 3650, retain_audit_days: 2920,
      special_committee: true, audit_committee: true, board_of_management: false,
      board_review_frequency: "quarterly",
    },
    attestation: { approved_by: "risk_committee", approved_on: "", reference: "", note: "" },
  },
}

// Configs are immutable once created (see config_service/app/models.py) - there is no
// "edit", only a new version to activate - so Duplicate has to propose a version number
// that won't collide with the one it copied from. Bumps the trailing numeric segment:
// "1.0.1" -> "1.0.2"; falls back to appending a segment if it doesn't end in a number.
export function bumpVersion(v: string): string {
  const parts = String(v || "1.0.0").split(".")
  const last = parseInt(parts[parts.length - 1], 10)
  if (Number.isFinite(last)) {
    parts[parts.length - 1] = String(last + 1)
    return parts.join(".")
  }
  return `${v}.1`
}

/** The operative threshold in a rule body: the band flagged operative carries it. */
export function operativeThreshold(body: Record<string, unknown> | null | undefined): number | null {
  const bands = (body?.config as Record<string, unknown> | undefined)?.bands
  if (!Array.isArray(bands) || !bands.length) return null
  const band = bands.find((b) => b.operative) ?? bands[bands.length - 1]
  const v = band.lowerLimit ?? band.upperLimit
  return v === undefined || v === null || v === "" ? null : Number(v)
}

// Lane A: exactly the set decision-service's inline lane evaluates in the payment
// window - copied from services/decision_service/app/inline_rules.py's INLINE_SOURCE,
// not re-derived, so this list cannot silently drift from what actually runs inline.
// Lane C: no dedicated engine exists yet (see docs/LANE_C_DESIGN_AND_USE_CASES.md) - the
// QUAL-* rules are its closest built analogue: qualitative, entered by credit monitoring
// rather than computed from a transaction, same theme as Lane C's periodic borrower
// assessment. They are scored by the same near-real-time engine as Lane B today.
// Everything else "rule"-kind is Lane B, the near-real-time engine's own set.
export const LANE_A_RULE_IDS = new Set([
  "VEL-01", "VEL-03", "SME-01", "SME-02", "BEH-01", "LAY-01", "LAY-02", "LAY-04", "CHN-01",
])
export const LANE_C_RULE_IDS = new Set(["QUAL-01", "QUAL-02", "QUAL-03"])

export type Lane = "a" | "b" | "c"

export const LANE_NOTES: Record<Lane, string> = {
  a: "Lane A — evaluated inline, inside the payment authorisation window (POST /decide). "
    + "Exactly the rule ids decision-service's inline lane can measure from account "
    + "counters or from what the channel already sends.",
  b: "Lane B — scored near-real-time, after ingestion, with full account and network "
    + "context. Everything not eligible for Lane A or thematically Lane C.",
  c: "Lane C — \"Periodic Borrower Assessment\" has no dedicated scoring engine yet "
    + "(see docs/LANE_C_DESIGN_AND_USE_CASES.md). These qualitative, credit-monitoring-"
    + "entered rules are its closest built analogue today; they are still scored by the "
    + "same near-real-time engine as Lane B, not a separate one.",
}

export function laneOf(cfg: ConfigOut): Lane | null {
  if (cfg.kind !== "rule") return null
  if (LANE_A_RULE_IDS.has(cfg.name)) return "a"
  if (LANE_C_RULE_IDS.has(cfg.name)) return "c"
  return "b"
}
