"""rule comparator on stored configs

Rule bodies gained a ``comparator`` field. New tenants get it from the catalogue at seed
time; tenants onboarded before this did not, and the seeder deliberately skips configs
that already exist rather than overwriting a bank's retuned thresholds.

Without this backfill those tenants keep the historical "gte" behaviour for the two
age-based indicators, so VEL-02 and CPT-01 compare a beneficiary's age in hours against 24
the wrong way round and effectively never fire. A silently dormant fraud indicator is
exactly what an RBI inspection looks for, so it is corrected here rather than left to a
re-seed that may never happen.

Only the comparator is touched. Thresholds a bank has tuned are theirs.

Revision ID: 7d76cf9fab53
Revises: 8ee0a7beb988
Create Date: 2026-07-30 16:50:55.908389

"""
from typing import Sequence, Union

from alembic import op
from sqlalchemy import text

# revision identifiers, used by Alembic.
revision: str = '7d76cf9fab53'
down_revision: Union[str, None] = '8ee0a7beb988'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Kept literal rather than imported: a migration must describe the world as it was when it
# ran, and must not change meaning because application code moved on.
BELOW_THRESHOLD_RULES = ("VEL-02", "CPT-01")


def upgrade() -> None:
    conn = op.get_bind()
    conn.execute(text(
        "UPDATE config.tenant_configs "
        "SET body = jsonb_set(body::jsonb, '{comparator}', '\"lte\"'::jsonb, true) "
        "WHERE kind = 'rule' AND name = ANY(:names)"),
        {"names": list(BELOW_THRESHOLD_RULES)})
    conn.execute(text(
        "UPDATE config.tenant_configs "
        "SET body = jsonb_set(body::jsonb, '{comparator}', '\"gte\"'::jsonb, true) "
        "WHERE kind = 'rule' AND NOT (name = ANY(:names)) "
        "  AND NOT (body::jsonb ? 'comparator')"),
        {"names": list(BELOW_THRESHOLD_RULES)})


def downgrade() -> None:
    op.execute("UPDATE config.tenant_configs "
               "SET body = (body::jsonb - 'comparator') WHERE kind = 'rule'")
