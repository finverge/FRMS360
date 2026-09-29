"""Role-based access control for the FRMS application.

One application, several modules. A role grants access to modules, and — within the
monitoring module — to specific dashboards. Defining this in one table keeps the API
and the console navigation from drifting apart: the console asks the server what it may
show rather than hard-coding its own idea of the rules.

Enforcement is server-side. The module list returned to the UI only decides what is
*rendered*; every endpoint independently checks the caller's role.
"""
from dataclasses import dataclass

# ---- modules ----
MOD_CONTROL_PLANE = "control_plane"   # tenants, branding, users, detection configs
MOD_MONITORING = "monitoring"         # FRMS/EWS dashboards
MOD_AUDIT = "audit"                   # activity trail
MOD_ADMIN = "administration"          # platform-wide administration

ALL_MODULES = [MOD_CONTROL_PLANE, MOD_MONITORING, MOD_AUDIT, MOD_ADMIN]

MODULE_META = {
    MOD_CONTROL_PLANE: {"label": "Control Plane", "icon": "◧",
                        "description": "Tenants, branding, users and detection configuration"},
    MOD_MONITORING: {"label": "Monitoring", "icon": "◕",
                     "description": "FRMS and EWS dashboards with drill-down"},
    MOD_AUDIT: {"label": "Audit", "icon": "◔",
                "description": "Activity trail and evidence"},
    MOD_ADMIN: {"label": "Administration", "icon": "◆",
                "description": "Platform-wide administration"},
}

# ---- dashboards (within the monitoring module) ----
DASHBOARDS = [
    # Ordered by the spec's delivery priority: P1 first, then P2, then P3.
    "analyst", "ews", "rfa", "board", "supervisor",          # P1
    "realtime", "aml", "account360",                          # P2
    "investigator", "model", "tenant_health",                 # P3
    "risk_manager", "inspection",                             # beyond the original catalogue
]
DASHBOARD_META = {
    "analyst":      {"label": "Analyst",      "persona": "L1 Fraud Analyst",
                     "question": "What do I work next, and is it real?"},
    "investigator": {"label": "Investigator", "persona": "L2 Senior Investigator",
                     "question": "Is this one mule, or a ring?"},
    "risk_manager": {"label": "Operations",   "persona": "Fraud Risk Manager",
                     "question": "Where is the queue failing, and why?"},
    "aml":          {"label": "AML / STR",    "persona": "Principal Officer (PMLA)",
                     "question": "What must be filed with FIU-IND, by when?"},
    "board":        {"label": "Board",        "persona": "CRO / ACB / Special Committee",
                     "question": "What is our exposure and are we compliant?"},
    "supervisor":   {"label": "Compliance",   "persona": "Compliance Supervisor",
                     "question": "Are the regulatory clocks being met?"},
    "model":        {"label": "Model Risk",   "persona": "Model Risk / Data Science",
                     "question": "Is the model still fit, fair and explainable?"},
    "inspection":   {"label": "Inspection",   "persona": "Internal Audit / RBI Inspection",
                     "question": "Prove why this account was flagged."},
    "ews":          {"label": "EWS Signals",  "persona": "Fraud Risk Manager / Analyst",
                     "question": "Which indicators are firing - and which are silent?"},
    "rfa":          {"label": "RFA Lifecycle","persona": "Investigator / Compliance",
                     "question": "Where is each red-flagged account in its lifecycle?"},
    "realtime":     {"label": "Real-Time",    "persona": "Operations",
                     "question": "What is flowing right now, and what are we stopping?"},
    "account360":   {"label": "Account 360",  "persona": "Investigator",
                     "question": "Everything known about one account."},
    "tenant_health":{"label": "Tenant Health","persona": "Platform Administrator",
                     "question": "Which bank's pipeline is degraded?"},
}


@dataclass(frozen=True)
class Role:
    name: str
    label: str
    modules: tuple[str, ...]
    dashboards: tuple[str, ...]
    tenant_scoped: bool      # False = may act across tenants
    can_admin_tenant: bool   # may change branding/users/configs for its tenant
    # May unmask customer PII (account numbers, device ids, IPs) with a justification.
    # Deliberately FALSE for platform staff: operating the service does not require
    # seeing a bank's customers, and an outsourcing review will ask exactly this.
    # Also false for aggregate-only roles (board, CRO) and for model work, which must
    # run on masked data.
    can_reveal_pii: bool = False
    # May propose or confirm a configuration activation (BR-715 maker-checker) -
    # narrower than can_admin_tenant. risk_manager owns fraud thresholds day-to-day
    # under BR-104/BR-311 without carrying full tenant admin, and this is the flag
    # that says so - the "risk_manager-equivalent" grant a custom role can now also
    # be given without needing can_admin_tenant too.
    can_activate_config: bool = False


_ALL_DASH = tuple(DASHBOARDS)
# Tenant Health is platform-operations telemetry (ingestion lag, feed health across the
# fleet). A bank's own administrator has no business in it, so it is excluded from every
# tenant-scoped role - including tenant_admin, which otherwise gets everything.
_TENANT_DASH = tuple(d for d in DASHBOARDS if d != "tenant_health")

ROLES: dict[str, Role] = {
    # ---- platform / administration ----
    # NOTE can_reveal_pii=False: operating the service does not require seeing a bank's
    # customers. This is the outsourcing boundary an RBI review will probe.
    "platform_admin": Role(
        "platform_admin", "Platform Administrator",
        (MOD_CONTROL_PLANE, MOD_MONITORING, MOD_AUDIT, MOD_ADMIN),
        _ALL_DASH, tenant_scoped=False, can_admin_tenant=True, can_reveal_pii=False,
        can_activate_config=True),

    "tenant_admin": Role(
        "tenant_admin", "Tenant Administrator",
        (MOD_CONTROL_PLANE, MOD_MONITORING, MOD_AUDIT),
        _TENANT_DASH, tenant_scoped=True, can_admin_tenant=True, can_reveal_pii=True,
        can_activate_config=True),

    # ---- first line: detection & investigation ----
    "analyst": Role(
        "analyst", "Fraud Analyst (L1)", (MOD_MONITORING,),
        ("analyst", "ews", "realtime"),
        tenant_scoped=True, can_admin_tenant=False, can_reveal_pii=True),

    "investigator": Role(
        "investigator", "Senior Investigator (L2)", (MOD_MONITORING,),
        # Needs the queue it inherits from L1 as well as the case/network view.
        ("investigator", "account360", "rfa", "ews", "analyst"),
        tenant_scoped=True, can_admin_tenant=False, can_reveal_pii=True),

    "risk_manager": Role(
        "risk_manager", "Fraud Risk Manager", (MOD_MONITORING, MOD_AUDIT),
        # Runs the operation: queue health plus the detection quality behind it.
        ("risk_manager", "ews", "realtime", "analyst", "investigator", "rfa", "board"),
        tenant_scoped=True, can_admin_tenant=False, can_reveal_pii=True,
        # Owns fraud thresholds day-to-day under BR-104/BR-311, without needing full
        # tenant admin to activate a rule or policy version.
        can_activate_config=True),

    # ---- second line: compliance ----
    "principal_officer": Role(
        "principal_officer", "Principal Officer (PMLA)", (MOD_MONITORING, MOD_AUDIT),
        # PMLA obligation runs independently of the fraud-classification track.
        ("aml", "rfa", "supervisor"),
        tenant_scoped=True, can_admin_tenant=False, can_reveal_pii=True),

    "supervisor": Role(
        "supervisor", "Compliance Supervisor", (MOD_MONITORING, MOD_AUDIT),
        ("supervisor", "rfa", "aml", "ews", "board"),
        tenant_scoped=True, can_admin_tenant=False, can_reveal_pii=True),

    # ---- governance: aggregate only, so no PII reveal ----
    "board": Role(
        "board", "Board / Executive", (MOD_MONITORING,),
        ("board",), tenant_scoped=True, can_admin_tenant=False, can_reveal_pii=False),

    "cro": Role(
        "cro", "CRO / ACB / Special Committee", (MOD_MONITORING, MOD_AUDIT),
        # Governance needs exposure AND compliance posture, not one or the other.
        ("board", "supervisor", "rfa", "risk_manager", "aml", "ews"),
        tenant_scoped=True, can_admin_tenant=False, can_reveal_pii=False),

    # ---- model governance (FREE-AI): models must be built on masked data ----
    "data_scientist": Role(
        "data_scientist", "Model Risk / Data Science", (MOD_MONITORING,),
        ("model", "ews", "analyst"),
        tenant_scoped=True, can_admin_tenant=False, can_reveal_pii=False),

    # ---- assurance: read-only evidence retrieval ----
    "rbi_inspector": Role(
        "rbi_inspector", "Internal Audit / RBI Inspection",
        (MOD_MONITORING, MOD_AUDIT),
        # Sees everything, changes nothing - enforced by having no admin capability
        # and no control-plane module.
        ("inspection", "rfa", "supervisor", "board", "aml", "model", "ews", "account360"),
        tenant_scoped=True, can_admin_tenant=False, can_reveal_pii=True),
}

# Roles a tenant administrator may hand out inside their own bank.
ASSIGNABLE_TENANT_ROLES = [
    "tenant_admin", "analyst", "investigator", "risk_manager",
    "principal_officer", "supervisor", "board", "cro",
    "data_scientist", "rbi_inspector",
]


def get_role(name: str) -> Role:
    return ROLES.get(name) or ROLES["analyst"]


def modules_for(role_name: str) -> list[dict]:
    role = get_role(role_name)
    return [{"key": m, **MODULE_META[m]} for m in role.modules]


def dashboards_for(role_name: str) -> list[dict]:
    role = get_role(role_name)
    return [{"key": d, **DASHBOARD_META[d]} for d in role.dashboards]


def can_access_module(role_name: str, module: str) -> bool:
    return module in get_role(role_name).modules


def can_access_dashboard(role_name: str, dashboard: str) -> bool:
    return dashboard in get_role(role_name).dashboards


def can_admin_tenant(role_name: str) -> bool:
    return get_role(role_name).can_admin_tenant


def can_reveal_pii(role_name: str) -> bool:
    return get_role(role_name).can_reveal_pii


def can_activate_config(role_name: str) -> bool:
    return get_role(role_name).can_activate_config
