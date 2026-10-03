"""Roles become data: a permissions list and a description on every tenant role, and every
tenant materialised.

Revision ID: e2b5c8d1f4a7
Revises: d1a4b6c8e9f0
Create Date: 2026-10-02

Before this, the ten built-in roles were decided by code (an in-process catalogue, a dozen
per-endpoint lists of role names and a role list on every case-workflow transition), and only
a tenant's custom roles were read from ``tenant.tenant_roles``. Editing a built-in role stored
the edit and changed nothing. From here the table is the only authority, so:

* ``permissions`` (the gated actions a role may perform) and ``description`` are added;
* every tenant gets a row for each starter role it lacks, so no tenant is left depending on
  a fallback that no longer exists;
* rows that are starter roles take that starter's permissions (exactly what the old lists
  granted), and any other role that held ``can_admin_tenant`` is given every gated action,
  because the old code let an administrator do anything implicitly.

The starter grants are frozen here on purpose: this migration must mean the same thing
however the in-code templates change later.
"""
from typing import Sequence, Union
import json
import uuid

from alembic import op
import sqlalchemy as sa

revision: str = "e2b5c8d1f4a7"
down_revision: Union[str, None] = "d1a4b6c8e9f0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

STARTER = {'tenant_admin': {'label': 'Tenant Administrator',
                  'modules': ['control_plane', 'monitoring', 'audit'],
                  'dashboards': ['analyst',
                                 'ews',
                                 'rfa',
                                 'board',
                                 'supervisor',
                                 'realtime',
                                 'aml',
                                 'account360',
                                 'investigator',
                                 'model',
                                 'risk_manager',
                                 'inspection'],
                  'can_admin_tenant': True,
                  'can_reveal_pii': True,
                  'can_activate_config': True,
                  'permissions': ['case.assign',
                                  'case.examine',
                                  'case.conclude',
                                  'case.approve_transition',
                                  'case.act.flag_rfa',
                                  'case.act.close_no_fraud',
                                  'case.act.revoke_rfa',
                                  'case.act.issue_show_cause',
                                  'case.act.record_response',
                                  'case.act.close_window',
                                  'case.act.declare_fraud',
                                  'case.act.exonerate',
                                  'case.act.file_fmr',
                                  'case.act.close_case',
                                  'case.act.reopen',
                                  'recovery.record',
                                  'detection.simulate',
                                  'detection.replay',
                                  'reference.load',
                                  'filing.submit',
                                  'ctr.file',
                                  'board_pack.prepare',
                                  'board_pack.issue',
                                  'usage.view',
                                  'machine_credential.manage'],
                  'description': "You administer your bank's workspace: users, roles, sign-in, "
                                 'notifications and detection rules, with the monitoring '
                                 'dashboards behind them.'},
 'analyst': {'label': 'Fraud Analyst (L1)',
             'modules': ['monitoring'],
             'dashboards': ['analyst', 'ews', 'realtime'],
             'can_admin_tenant': False,
             'can_reveal_pii': True,
             'can_activate_config': False,
             'permissions': ['detection.simulate', 'case.act.close_no_fraud'],
             'description': 'You work the first line: alerts waiting for review, and deciding '
                            'which are real.'},
 'investigator': {'label': 'Senior Investigator (L2)',
                  'modules': ['monitoring'],
                  'dashboards': ['investigator', 'account360', 'rfa', 'ews', 'analyst'],
                  'can_admin_tenant': False,
                  'can_reveal_pii': True,
                  'can_activate_config': False,
                  'permissions': ['case.assign',
                                  'case.examine',
                                  'recovery.record',
                                  'detection.simulate',
                                  'case.act.flag_rfa',
                                  'case.act.close_no_fraud',
                                  'case.act.issue_show_cause',
                                  'case.act.record_response',
                                  'case.act.close_window',
                                  'case.act.declare_fraud',
                                  'case.act.exonerate'],
                  'description': 'You take escalated alerts further: is this one mule account, or '
                                 'a ring?'},
 'risk_manager': {'label': 'Fraud Risk Manager',
                  'modules': ['monitoring', 'audit'],
                  'dashboards': ['risk_manager',
                                 'ews',
                                 'realtime',
                                 'analyst',
                                 'investigator',
                                 'rfa',
                                 'board'],
                  'can_admin_tenant': False,
                  'can_reveal_pii': True,
                  'can_activate_config': True,
                  'permissions': ['case.assign',
                                  'case.examine',
                                  'recovery.record',
                                  'detection.simulate',
                                  'case.conclude',
                                  'case.approve_transition',
                                  'detection.replay',
                                  'reference.load',
                                  'board_pack.issue',
                                  'filing.submit',
                                  'ctr.file',
                                  'board_pack.prepare',
                                  'case.act.flag_rfa',
                                  'case.act.close_no_fraud',
                                  'case.act.revoke_rfa',
                                  'case.act.issue_show_cause',
                                  'case.act.record_response',
                                  'case.act.close_window',
                                  'case.act.declare_fraud',
                                  'case.act.exonerate',
                                  'case.act.file_fmr',
                                  'case.act.close_case',
                                  'case.act.reopen'],
                  'description': 'You run the fraud operation: where the queue is failing, and the '
                                 'detection quality behind it.'},
 'principal_officer': {'label': 'Principal Officer (PMLA)',
                       'modules': ['monitoring', 'audit'],
                       'dashboards': ['aml', 'rfa', 'supervisor'],
                       'can_admin_tenant': False,
                       'can_reveal_pii': True,
                       'can_activate_config': False,
                       'permissions': ['case.assign',
                                       'case.examine',
                                       'recovery.record',
                                       'detection.simulate',
                                       'case.conclude',
                                       'case.approve_transition',
                                       'detection.replay',
                                       'reference.load',
                                       'board_pack.issue',
                                       'filing.submit',
                                       'ctr.file',
                                       'board_pack.prepare',
                                       'case.act.issue_show_cause',
                                       'case.act.record_response',
                                       'case.act.close_window',
                                       'case.act.declare_fraud',
                                       'case.act.exonerate',
                                       'case.act.file_fmr'],
                       'description': 'You own the PMLA obligation: what must be filed with '
                                      'FIU-IND, and by when.'},
 'supervisor': {'label': 'Compliance Supervisor',
                'modules': ['monitoring', 'audit'],
                'dashboards': ['supervisor', 'rfa', 'aml', 'ews', 'board'],
                'can_admin_tenant': False,
                'can_reveal_pii': True,
                'can_activate_config': False,
                'permissions': ['case.assign',
                                'case.examine',
                                'recovery.record',
                                'detection.simulate',
                                'filing.submit',
                                'ctr.file',
                                'board_pack.prepare',
                                'case.act.file_fmr',
                                'case.act.close_case',
                                'case.act.reopen'],
                'description': 'You watch the regulatory clocks: natural-justice deadlines, FMR '
                               'and STR filing dates.'},
 'board': {'label': 'Board / Executive',
           'modules': ['monitoring'],
           'dashboards': ['board'],
           'can_admin_tenant': False,
           'can_reveal_pii': False,
           'can_activate_config': False,
           'permissions': ['usage.view'],
           'description': "You see the bank's fraud exposure and compliance position at a glance. "
                          'Figures only; no customer data.'},
 'cro': {'label': 'CRO / ACB / Special Committee',
         'modules': ['monitoring', 'audit'],
         'dashboards': ['board', 'supervisor', 'rfa', 'risk_manager', 'aml', 'ews'],
         'can_admin_tenant': False,
         'can_reveal_pii': False,
         'can_activate_config': False,
         'permissions': ['board_pack.prepare', 'board_pack.issue', 'usage.view'],
         'description': 'You see exposure and compliance posture together, for the committee. '
                        'Figures only; no customer data.'},
 'data_scientist': {'label': 'Model Risk / Data Science',
                    'modules': ['monitoring'],
                    'dashboards': ['model', 'ews', 'analyst'],
                    'can_admin_tenant': False,
                    'can_reveal_pii': False,
                    'can_activate_config': False,
                    'permissions': [],
                    'description': 'You check that the models are still fit, fair and explainable. '
                                   'You work on masked data.'},
 'rbi_inspector': {'label': 'Internal Audit / RBI Inspection',
                   'modules': ['monitoring', 'audit'],
                   'dashboards': ['inspection',
                                  'rfa',
                                  'supervisor',
                                  'board',
                                  'aml',
                                  'model',
                                  'ews',
                                  'account360'],
                   'can_admin_tenant': False,
                   'can_reveal_pii': True,
                   'can_activate_config': False,
                   'permissions': [],
                   'description': 'You retrieve evidence: why an account was flagged, and what the '
                                  'bank did about it. Read-only.'}}

ALL_PERMISSIONS = ['case.assign',
 'case.examine',
 'case.conclude',
 'case.approve_transition',
 'case.act.flag_rfa',
 'case.act.close_no_fraud',
 'case.act.revoke_rfa',
 'case.act.issue_show_cause',
 'case.act.record_response',
 'case.act.close_window',
 'case.act.declare_fraud',
 'case.act.exonerate',
 'case.act.file_fmr',
 'case.act.close_case',
 'case.act.reopen',
 'recovery.record',
 'detection.simulate',
 'detection.replay',
 'reference.load',
 'filing.submit',
 'ctr.file',
 'board_pack.prepare',
 'board_pack.issue',
 'usage.view',
 'machine_credential.manage']


def upgrade() -> None:
    op.add_column("tenant_roles", sa.Column("permissions", sa.JSON(), nullable=False,
                                            server_default=sa.text("'[]'")), schema="tenant")
    op.add_column("tenant_roles", sa.Column("description", sa.String(length=600), nullable=False,
                                            server_default=""), schema="tenant")
    bind = op.get_bind()
    tenants = [r[0] for r in bind.execute(sa.text("SELECT id FROM tenant.tenants"))]
    for tid in tenants:
        have = {r[0] for r in bind.execute(
            sa.text("SELECT name FROM tenant.tenant_roles WHERE tenant_id = :t"), {"t": tid})}
        for name, spec in STARTER.items():
            if name in have:
                bind.execute(sa.text(
                    "UPDATE tenant.tenant_roles SET permissions = CAST(:p AS json), "
                    "description = :d WHERE tenant_id = :t AND name = :n"),
                    {"p": json.dumps(spec["permissions"]), "d": spec["description"],
                     "t": tid, "n": name})
            else:
                bind.execute(sa.text(
                    "INSERT INTO tenant.tenant_roles (id, tenant_id, name, label, modules, "
                    "dashboards, can_admin_tenant, can_reveal_pii, can_activate_config, source, "
                    "permissions, description) VALUES (:id, :t, :n, :l, CAST(:m AS json), "
                    "CAST(:db AS json), :a, :r, :c, 'seeded', CAST(:p AS json), :d)"),
                    {"id": str(uuid.uuid4()), "t": tid, "n": name, "l": spec["label"],
                     "m": json.dumps(spec["modules"]), "db": json.dumps(spec["dashboards"]),
                     "a": spec["can_admin_tenant"], "r": spec["can_reveal_pii"],
                     "c": spec["can_activate_config"], "p": json.dumps(spec["permissions"]),
                     "d": spec["description"]})
        # A custom role that was an administrator used to be allowed everything implicitly.
        bind.execute(sa.text(
            "UPDATE tenant.tenant_roles SET permissions = CAST(:p AS json) "
            "WHERE tenant_id = :t AND can_admin_tenant AND name NOT IN :starters"
        ).bindparams(sa.bindparam("starters", expanding=True)),
            {"p": json.dumps(ALL_PERMISSIONS), "t": tid, "starters": list(STARTER)})


def downgrade() -> None:
    op.drop_column("tenant_roles", "description", schema="tenant")
    op.drop_column("tenant_roles", "permissions", schema="tenant")
