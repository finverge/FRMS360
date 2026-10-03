"""Starter role templates and the module/dashboard catalogue. NOT the authority.

Who may do what is data: each tenant has its own role rows in ``tenant.tenant_roles``,
which its administrators create, change and delete from the console, and every service
decides from those rows (``cp_common.dynamic_roles``). Nothing here is consulted to allow
or refuse a request.

What this module still provides:

* ``MODULE_META`` / ``DASHBOARD_META`` - the modules and dashboards that exist, with
  their labels, so a role can be granted them. (``permissions.PERMISSIONS`` is the same
  idea for gated actions.)
* ``ROLES`` - the **starter templates** copied into a tenant's own rows when it is
  onboarded, and by the data migration that materialised them for existing tenants. After
  that copy a tenant's roles are its own: editing, narrowing, widening or deleting one
  never touches the template or any other tenant.

The one role that is not a tenant role is ``platform_admin``: Finverge operating staff,
who work across tenants and belong to no bank's catalogue. It is recognised structurally
(``Principal.is_platform_admin``), not granted by a table a tenant can edit.
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
    # Gated actions this role may perform (keys of cp_common.permissions.PERMISSIONS).
    permissions: tuple[str, ...] = ()
    # Shown to the person on their home page; an administrator edits it with the role.
    description: str = ""


_ALL_DASH = tuple(DASHBOARDS)
# Tenant Health is platform-operations telemetry (ingestion lag, feed health across the
# fleet). A bank's own administrator has no business in it, so it is excluded from every
# tenant-scoped role - including tenant_admin, which otherwise gets everything.
_TENANT_DASH = tuple(d for d in DASHBOARDS if d != "tenant_health")

_BASE_ROLES: dict[str, Role] = {
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

# Starter grants and the text each person sees on their home page. These reproduce what the
# per-endpoint role lists used to hard-code, so a tenant onboarded today behaves exactly as
# before until its administrator changes a role. A tenant administrator holds every gated
# action explicitly (there is no implicit "admin may do anything" in code any more).
from .permissions import ALL_PERMISSIONS, case_action  # noqa: E402

# The case-workflow actions each starter role held when they were hard-coded per transition.
_ACTIONS_HELD = {'flag_rfa': ['investigator', 'risk_manager'], 'close_no_fraud': ['analyst', 'investigator', 'risk_manager'], 'revoke_rfa': ['risk_manager'], 'issue_show_cause': ['investigator', 'risk_manager', 'principal_officer'], 'record_response': ['investigator', 'risk_manager', 'principal_officer'], 'close_window': ['investigator', 'risk_manager', 'principal_officer'], 'declare_fraud': ['investigator', 'risk_manager', 'principal_officer'], 'exonerate': ['investigator', 'risk_manager', 'principal_officer'], 'file_fmr': ['supervisor', 'principal_officer', 'risk_manager'], 'close_case': ['supervisor', 'risk_manager'], 'reopen': ['risk_manager', 'supervisor']}


def _acts(role: str) -> tuple[str, ...]:
    return tuple(case_action(a) for a, who in _ACTIONS_HELD.items() if role in who)

_CASEWORK = ("case.assign", "case.examine", "recovery.record", "detection.simulate")
_COMPLIANCE = ("filing.submit", "ctr.file", "board_pack.prepare")
_SCREEN = ("sanctions.screen", "lane_c.view")       # read-only screening and credit health
_CREDIT_WORK = _SCREEN + ("lane_c.manage",)
_STARTER: dict[str, tuple[tuple[str, ...], str]] = {
    "platform_admin": ((), ""),
    "tenant_admin": (ALL_PERMISSIONS,
        "You administer your bank's workspace: users, roles, sign-in, notifications and "
        "detection rules, with the monitoring dashboards behind them."),
    "analyst": (("detection.simulate",) + _SCREEN + _acts("analyst"),
        "You work the first line: alerts waiting for review, and deciding which are real."),
    "investigator": (_CASEWORK + _SCREEN + _acts("investigator"),
        "You take escalated alerts further: is this one mule account, or a ring?"),
    "risk_manager": (_CASEWORK + ("case.conclude", "case.approve_transition", "detection.replay",
                                  "reference.load", "board_pack.issue") + _COMPLIANCE + _CREDIT_WORK + _acts("risk_manager"),
        "You run the fraud operation: where the queue is failing, and the detection quality behind it."),
    "principal_officer": (_CASEWORK + ("case.conclude", "case.approve_transition", "detection.replay",
                                       "reference.load", "board_pack.issue") + _COMPLIANCE + _CREDIT_WORK + _acts("principal_officer"),
        "You own the PMLA obligation: what must be filed with FIU-IND, and by when."),
    "supervisor": (_CASEWORK + _COMPLIANCE + _SCREEN + _acts("supervisor"),
        "You watch the regulatory clocks: natural-justice deadlines, FMR and STR filing dates."),
    "board": (("usage.view",),
        "You see the bank's fraud exposure and compliance position at a glance. Figures only; no customer data."),
    "cro": (("board_pack.prepare", "board_pack.issue", "usage.view"),
        "You see exposure and compliance posture together, for the committee. Figures only; no customer data."),
    "data_scientist": ((),
        "You check that the models are still fit, fair and explainable. You work on masked data."),
    "rbi_inspector": (_SCREEN,
        "You retrieve evidence: why an account was flagged, and what the bank did about it. Read-only."),
}

ROLES: dict[str, Role] = {
    name: Role(r.name, r.label, r.modules, r.dashboards, r.tenant_scoped, r.can_admin_tenant,
               r.can_reveal_pii, r.can_activate_config, _STARTER[name][0], _STARTER[name][1])
    for name, r in _BASE_ROLES.items()
}

# The starter roles copied into a tenant's own rows at onboarding (see roles.seed_default_roles).
# This is the seeding list, not a list of what a tenant may assign: that is its own role rows.
ASSIGNABLE_TENANT_ROLES = [
    "tenant_admin", "analyst", "investigator", "risk_manager",
    "principal_officer", "supervisor", "board", "cro",
    "data_scientist", "rbi_inspector",
]
