"""Lane C manual findings: the entry point for LNC-15/LNC-16, the two RBI signals that
are physical/qualitative facts no document or feed can ever carry automatically.

Revision ID: c7d2e5f9a1b3
Revises: b3f7a9c1d4e2
Create Date: 2026-09-30 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'c7d2e5f9a1b3'
down_revision: Union[str, None] = 'b3f7a9c1d4e2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

SCHEMA = "lane_c"


def upgrade() -> None:
    op.create_table(
        'manual_findings',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('tenant_id', sa.String(length=36), nullable=False),
        sa.Column('account', sa.String(length=40), nullable=False),
        sa.Column('reporting_date', sa.Date(), nullable=False),
        sa.Column('signal_code', sa.String(length=12), nullable=False),
        sa.Column('finding', sa.Boolean(), server_default='false', nullable=False),
        sa.Column('notes', sa.Text(), server_default='', nullable=False),
        sa.Column('submitted_by', sa.String(length=255), nullable=False),
        sa.Column('submitted_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('tenant_id', 'account', 'reporting_date', 'signal_code',
                            name='uq_manual_finding_period'),
        schema=SCHEMA,
    )
    op.create_index(op.f('ix_lane_c_manual_findings_tenant_id'), 'manual_findings',
                    ['tenant_id'], schema=SCHEMA)
    op.create_index(op.f('ix_lane_c_manual_findings_account'), 'manual_findings',
                    ['account'], schema=SCHEMA)


def downgrade() -> None:
    op.drop_table('manual_findings', schema=SCHEMA)
