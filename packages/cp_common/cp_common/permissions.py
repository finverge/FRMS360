"""The catalogue of actions the platform can gate. Not who may do them.

Code has to name an action in order to guard it, so this lists which gated actions exist,
with a label for the console. Which role may perform which action is **data**: it lives on
each tenant's role rows (``tenant.tenant_roles.permissions``) and an administrator changes
it from the console. Nothing in the services decides by role name.

Adding a gated action means adding it here and guarding the endpoint with
``cp_common.dynamic_roles.has_permission``; a tenant then grants it to whichever of its
roles it chooses. The starter grants new tenants receive are in ``rbac.ROLES``.
"""

PERMISSIONS: dict[str, dict[str, str]] = {
    "case.assign": {"group": "Casework", "label": "Assign cases to people"},
    "case.examine": {"group": "Casework", "label": "Run the staff accountability examination"},
    "case.conclude": {"group": "Casework", "label": "Conclude the accountability examination"},
    "case.approve_transition": {"group": "Casework", "label": "Approve a case action as the second person"},
    "case.act.flag_rfa": {"group": "Case actions", "label": "Flag an account as red-flagged"},
    "case.act.close_no_fraud": {"group": "Case actions", "label": "Close a case as no fraud"},
    "case.act.revoke_rfa": {"group": "Case actions", "label": "Revoke a red-flag"},
    "case.act.issue_show_cause": {"group": "Case actions", "label": "Issue a show-cause notice"},
    "case.act.record_response": {"group": "Case actions", "label": "Record the borrower's response"},
    "case.act.close_window": {"group": "Case actions", "label": "Close the response window"},
    "case.act.declare_fraud": {"group": "Case actions", "label": "Declare fraud"},
    "case.act.exonerate": {"group": "Case actions", "label": "Exonerate after the response"},
    "case.act.file_fmr": {"group": "Case actions", "label": "File the fraud monitoring report"},
    "case.act.close_case": {"group": "Case actions", "label": "Close a reported case"},
    "case.act.reopen": {"group": "Case actions", "label": "Reopen an exonerated case"},
    "recovery.record": {"group": "Casework", "label": "Record recoveries and agency referrals"},
    "detection.simulate": {"group": "Detection", "label": "Re-score history to test a threshold (writes nothing)"},
    "detection.replay": {"group": "Detection", "label": "Replay detection over history (writes alerts)"},
    "reference.load": {"group": "Detection", "label": "Load screening and reference lists"},
    "filing.submit": {"group": "Regulatory returns", "label": "Prepare and submit FMR and STR returns"},
    "ctr.file": {"group": "Regulatory returns", "label": "Prepare and file cash transaction reports"},
    "board_pack.prepare": {"group": "Governance", "label": "Prepare board and committee packs"},
    "board_pack.issue": {"group": "Governance", "label": "Issue a pack to the committee"},
    "usage.view": {"group": "Governance", "label": "View metered usage and billing figures"},
    "sanctions.screen": {"group": "Screening and credit", "label": "Screen a name against the sanctions list"},
    "lane_c.view": {"group": "Screening and credit", "label": "View borrower credit health: scores, signals and alerts"},
    "lane_c.manage": {"group": "Screening and credit", "label": "Upload statements, review alerts and record borrower findings"},
    "machine_credential.manage": {"group": "Integration", "label": "Issue and revoke machine credentials"},
}

ALL_PERMISSIONS: tuple[str, ...] = tuple(PERMISSIONS)


def unknown(keys) -> set[str]:
    """Keys that are not gated actions - refused when a role is written."""
    return set(keys) - set(PERMISSIONS)


#: One permission per case-workflow action (analytics_service/app/workflow.py), named
#: ``case.act.<action>``. A test keeps this list and the workflow's transitions identical.
def case_action(action: str) -> str:
    return "case.act." + action
