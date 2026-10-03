"""Permissions for sanctions screening and borrower credit health (Lane C).

Revision ID: f3c6d9e2a5b8
Revises: e2b5c8d1f4a7
Create Date: 2026-10-02

Both features used to be open to every signed-in user of a tenant: their endpoints checked
only the tenant, so a Board member or a data scientist could read borrower-level credit data
and run sanctions screens. They are now gated by three permissions on the tenant's own role
rows:

* ``sanctions.screen``  - screen a name against the sanctions list;
* ``lane_c.view``       - read borrower scores, signals and alerts;
* ``lane_c.manage``     - upload statements, review alerts, record manual findings and project
                          appraisals.

Existing roles are granted what they effectively had, minus the roles that should never have
had it (aggregate-only and model-work roles see neither). The starter grants are frozen here
so the migration means the same thing however the in-code templates change later. Idempotent:
a permission a role already holds is not duplicated, and a tenant's own later edits are not
overwritten.
"""
from typing import Sequence, Union
import json

from alembic import op
import sqlalchemy as sa

revision: str = "f3c6d9e2a5b8"
down_revision: Union[str, None] = "e2b5c8d1f4a7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

NEW = ["sanctions.screen", "lane_c.view", "lane_c.manage"]

# Frozen starter grants for the new permissions, by starter role name.
GRANTS = {
    "tenant_admin": ["sanctions.screen", "lane_c.view", "lane_c.manage"],
    "analyst": ["sanctions.screen", "lane_c.view"],
    "investigator": ["sanctions.screen", "lane_c.view"],
    "risk_manager": ["sanctions.screen", "lane_c.view", "lane_c.manage"],
    "principal_officer": ["sanctions.screen", "lane_c.view", "lane_c.manage"],
    "supervisor": ["sanctions.screen", "lane_c.view"],
    "rbi_inspector": ["sanctions.screen", "lane_c.view"],
    # board, cro, data_scientist: aggregate-only or masked-data roles - none of the three.
}


def upgrade() -> None:
    bind = op.get_bind()
    rows = bind.execute(sa.text(
        "SELECT id, name, can_admin_tenant, permissions FROM tenant.tenant_roles")).all()
    for rid, name, is_admin, perms in rows:
        current = list(perms or [])
        if name in GRANTS:
            add = GRANTS[name]
        elif is_admin:
            add = NEW            # an administrator-capable custom role could always do these
        else:
            add = []
        merged = current + [p for p in add if p not in current]
        if merged != current:
            bind.execute(sa.text("UPDATE tenant.tenant_roles SET permissions = CAST(:p AS json) "
                                 "WHERE id = :i"), {"p": json.dumps(merged), "i": rid})


def downgrade() -> None:
    bind = op.get_bind()
    rows = bind.execute(sa.text("SELECT id, permissions FROM tenant.tenant_roles")).all()
    for rid, perms in rows:
        kept = [p for p in (perms or []) if p not in NEW]
        if kept != list(perms or []):
            bind.execute(sa.text("UPDATE tenant.tenant_roles SET permissions = CAST(:p AS json) "
                                 "WHERE id = :i"), {"p": json.dumps(kept), "i": rid})
