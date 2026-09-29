// Mirrors packages/cp_common/cp_common/rbac.py's MODULE_META/DASHBOARD_META and
// services/tenant_service/app/roles.py's TENANT_ALLOWED_* guardrails - the platform-only
// "administration" module and the "tenant_health" dashboard are never offered here, so a
// tenant admin can't even try to grant them (the server refuses it anyway; this just
// keeps the form from proposing something guaranteed to be rejected).
export interface CatalogueOption {
  key: string
  label: string
  description?: string
}

export const TENANT_MODULES: CatalogueOption[] = [
  { key: "control_plane", label: "Control Plane", description: "Tenants, branding, users and detection configuration" },
  { key: "monitoring", label: "Monitoring", description: "FRMS and EWS dashboards with drill-down" },
  { key: "audit", label: "Audit", description: "Activity trail and evidence" },
]

// Ordered by the spec's delivery priority: P1, then P2, then P3 - same order as
// cp_common.rbac.DASHBOARDS minus tenant_health (platform-only telemetry).
export const TENANT_DASHBOARDS: CatalogueOption[] = [
  { key: "analyst", label: "Analyst", description: "L1 Fraud Analyst — what to work next, and is it real?" },
  { key: "ews", label: "EWS Signals", description: "Fraud Risk Manager / Analyst — which indicators are firing?" },
  { key: "rfa", label: "RFA Lifecycle", description: "Investigator / Compliance — where is each red-flagged account?" },
  { key: "board", label: "Board", description: "CRO / ACB / Special Committee — exposure and compliance posture" },
  { key: "supervisor", label: "Compliance", description: "Compliance Supervisor — are the regulatory clocks being met?" },
  { key: "realtime", label: "Real-Time", description: "Operations — what is flowing right now?" },
  { key: "aml", label: "AML / STR", description: "Principal Officer (PMLA) — what must be filed with FIU-IND?" },
  { key: "account360", label: "Account 360", description: "Investigator — everything known about one account" },
  { key: "investigator", label: "Investigator", description: "L2 Senior Investigator — one mule, or a ring?" },
  { key: "model", label: "Model Risk", description: "Model Risk / Data Science — is the model still fit and fair?" },
  { key: "risk_manager", label: "Operations", description: "Fraud Risk Manager — where is the queue failing?" },
  { key: "inspection", label: "Inspection", description: "Internal Audit / RBI Inspection — prove why this was flagged" },
]

// The ten roles every tenant starts with (cp_common.rbac.ASSIGNABLE_TENANT_ROLES) -
// these can be edited but never deleted or renamed (routes/tenants.py's delete_role).
export const FIXED_ROLE_NAMES = new Set([
  "tenant_admin", "analyst", "investigator", "risk_manager",
  "principal_officer", "supervisor", "board", "cro",
  "data_scientist", "rbi_inspector",
])

// Same ten, with labels - for pickers that assign one of these roles to something else
// (an SSO group mapping, an invite). Kept separate from FIXED_ROLE_NAMES rather than
// derived from it so the order matches the platform catalogue's own presentation order.
// Deliberately NOT extended with a tenant's custom roles: unlike the invite flow
// (assignable_names(), which does include them), sso.py's _validate_role_fields checks
// group-mapped roles against this exact fixed set only - offering a custom role here
// would let an admin submit a mapping the server is guaranteed to reject.
export const ASSIGNABLE_ROLE_OPTIONS: CatalogueOption[] = [
  { key: "tenant_admin", label: "Tenant Administrator" },
  { key: "analyst", label: "Fraud Analyst (L1)" },
  { key: "investigator", label: "Senior Investigator (L2)" },
  { key: "risk_manager", label: "Fraud Risk Manager" },
  { key: "principal_officer", label: "Principal Officer (PMLA)" },
  { key: "supervisor", label: "Compliance Supervisor" },
  { key: "board", label: "Board / Executive" },
  { key: "cro", label: "CRO / ACB / Special Committee" },
  { key: "data_scientist", label: "Model Risk / Data Science" },
  { key: "rbi_inspector", label: "Internal Audit / RBI Inspection" },
]
