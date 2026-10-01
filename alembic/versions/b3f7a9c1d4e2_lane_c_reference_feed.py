"""Lane C's own reference-feed mechanism: reference_sources, reference_lists,
reference_entries - and a company_identifier column on financial_statements so
MCA/ROC and rating feeds (keyed by CIN/PAN) can be matched to the right borrower.

Revision ID: b3f7a9c1d4e2
Revises: 1adfb459ecd3
Create Date: 2026-09-30 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'b3f7a9c1d4e2'
down_revision: Union[str, None] = '1adfb459ecd3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

SCHEMA = "lane_c"


def upgrade() -> None:
    op.add_column('financial_statements',
                  sa.Column('company_identifier', sa.String(length=24), nullable=True),
                  schema=SCHEMA)

    op.create_table(
        'reference_sources',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('tenant_id', sa.String(length=36), nullable=False),
        sa.Column('kind', sa.String(length=32), nullable=False),
        sa.Column('url', sa.String(length=600), nullable=False),
        sa.Column('fmt', sa.String(length=8), server_default='lines', nullable=False),
        sa.Column('auth_header', sa.String(length=64), server_default='', nullable=False),
        sa.Column('auth_value', sa.Text(), server_default='', nullable=False),
        sa.Column('key_field', sa.String(length=64), server_default='', nullable=False),
        sa.Column('cadence_hours', sa.Integer(), server_default='24', nullable=False),
        sa.Column('max_shrink', sa.Float(), server_default='0.10', nullable=False),
        sa.Column('enabled', sa.Boolean(), server_default='true', nullable=False),
        sa.Column('last_attempt_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('last_success_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('last_checksum', sa.String(length=64), server_default='', nullable=False),
        sa.Column('last_entry_count', sa.Integer(), server_default='0', nullable=False),
        sa.Column('last_error', sa.Text(), server_default='', nullable=False),
        sa.Column('next_due_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('tenant_id', 'kind', name='uq_lane_c_reference_source_kind'),
        schema=SCHEMA,
    )
    op.create_index('ix_lane_c_reference_source_due', 'reference_sources',
                    ['enabled', 'next_due_at'], schema=SCHEMA)
    op.create_index(op.f('ix_lane_c_reference_sources_tenant_id'), 'reference_sources',
                    ['tenant_id'], schema=SCHEMA)
    op.create_index(op.f('ix_lane_c_reference_sources_kind'), 'reference_sources',
                    ['kind'], schema=SCHEMA)

    op.create_table(
        'reference_lists',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('tenant_id', sa.String(length=36), nullable=False),
        sa.Column('kind', sa.String(length=32), nullable=False),
        sa.Column('version', sa.String(length=80), nullable=False),
        sa.Column('source', sa.String(length=200), server_default='', nullable=False),
        sa.Column('active', sa.Boolean(), server_default='false', nullable=False),
        sa.Column('entry_count', sa.Integer(), server_default='0', nullable=False),
        sa.Column('checksum', sa.String(length=64), server_default='', nullable=False),
        sa.Column('loaded_by', sa.String(length=255), nullable=False),
        sa.Column('loaded_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('note', sa.Text(), server_default='', nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('tenant_id', 'kind', 'version', name='uq_lane_c_reflist_version'),
        schema=SCHEMA,
    )
    op.create_index('ix_lane_c_reflist_active', 'reference_lists',
                    ['tenant_id', 'kind', 'active'], schema=SCHEMA)
    op.create_index(op.f('ix_lane_c_reference_lists_tenant_id'), 'reference_lists',
                    ['tenant_id'], schema=SCHEMA)
    op.create_index(op.f('ix_lane_c_reference_lists_kind'), 'reference_lists',
                    ['kind'], schema=SCHEMA)

    op.create_table(
        'reference_entries',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('tenant_id', sa.String(length=36), nullable=False),
        sa.Column('list_id', sa.String(length=36), nullable=False),
        sa.Column('kind', sa.String(length=32), nullable=False),
        sa.Column('match_key', sa.String(length=64), nullable=False),
        sa.Column('display_name', sa.String(length=300), nullable=False),
        sa.Column('attributes', postgresql.JSONB(), server_default='{}', nullable=False),
        sa.PrimaryKeyConstraint('id'),
        schema=SCHEMA,
    )
    op.create_index('ix_lane_c_refentry_list_key', 'reference_entries',
                    ['list_id', 'match_key'], schema=SCHEMA)
    op.create_index('ix_lane_c_refentry_tenant', 'reference_entries',
                    ['tenant_id', 'kind'], schema=SCHEMA)
    op.create_index(op.f('ix_lane_c_reference_entries_tenant_id'), 'reference_entries',
                    ['tenant_id'], schema=SCHEMA)
    op.create_index(op.f('ix_lane_c_reference_entries_list_id'), 'reference_entries',
                    ['list_id'], schema=SCHEMA)


def downgrade() -> None:
    op.drop_table('reference_entries', schema=SCHEMA)
    op.drop_table('reference_lists', schema=SCHEMA)
    op.drop_table('reference_sources', schema=SCHEMA)
    op.drop_column('financial_statements', 'company_identifier', schema=SCHEMA)
