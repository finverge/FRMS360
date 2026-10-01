"""Lane C project appraisal: the sanctioned baseline and periodic progress submissions
LNC-02 (scope creep) and LNC-22 (cost variance) compare against - RBI 2016 illustrative
signals #3 and #33, neither measurable before this since no project-appraisal baseline
existed anywhere in Lane C.

Revision ID: d1a4b6c8e9f0
Revises: c7d2e5f9a1b3
Create Date: 2026-10-01 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'd1a4b6c8e9f0'
down_revision: Union[str, None] = 'c7d2e5f9a1b3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

SCHEMA = "lane_c"


def upgrade() -> None:
    op.create_table(
        'project_appraisals',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('tenant_id', sa.String(length=36), nullable=False),
        sa.Column('account', sa.String(length=40), nullable=False),
        sa.Column('sanctioned_cost_paise', sa.BigInteger(), nullable=False),
        sa.Column('sanctioned_completion_date', sa.Date(), nullable=False),
        sa.Column('submitted_by', sa.String(length=255), nullable=False),
        sa.Column('submitted_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('tenant_id', 'account', name='uq_project_appraisal_account'),
        schema=SCHEMA,
    )
    op.create_index(op.f('ix_lane_c_project_appraisals_tenant_id'), 'project_appraisals',
                    ['tenant_id'], schema=SCHEMA)
    op.create_index(op.f('ix_lane_c_project_appraisals_account'), 'project_appraisals',
                    ['account'], schema=SCHEMA)

    op.create_table(
        'project_progress',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('tenant_id', sa.String(length=36), nullable=False),
        sa.Column('account', sa.String(length=40), nullable=False),
        sa.Column('reporting_date', sa.Date(), nullable=False),
        sa.Column('actual_cost_incurred_paise', sa.BigInteger(), nullable=False),
        sa.Column('revised_completion_date', sa.Date(), nullable=True),
        sa.Column('notes', sa.Text(), server_default='', nullable=False),
        sa.Column('submitted_by', sa.String(length=255), nullable=False),
        sa.Column('submitted_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('tenant_id', 'account', 'reporting_date',
                            name='uq_project_progress_period'),
        schema=SCHEMA,
    )
    op.create_index(op.f('ix_lane_c_project_progress_tenant_id'), 'project_progress',
                    ['tenant_id'], schema=SCHEMA)
    op.create_index(op.f('ix_lane_c_project_progress_account'), 'project_progress',
                    ['account'], schema=SCHEMA)


def downgrade() -> None:
    op.drop_table('project_progress', schema=SCHEMA)
    op.drop_table('project_appraisals', schema=SCHEMA)
