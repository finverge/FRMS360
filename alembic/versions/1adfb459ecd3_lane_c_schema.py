"""Lane C schema: financial statements, parsed line items, computed signals, credit
health scores, alerts.

Revision ID: 1adfb459ecd3
Revises: 1ecffe99856a
Create Date: 2026-09-29 15:10:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '1adfb459ecd3'
down_revision: Union[str, None] = '1ecffe99856a'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

SCHEMA = "lane_c"


def upgrade() -> None:
    op.execute(f'CREATE SCHEMA IF NOT EXISTS "{SCHEMA}"')

    op.create_table(
        'financial_statements',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('tenant_id', sa.String(length=36), nullable=False),
        sa.Column('account', sa.String(length=40), nullable=False),
        sa.Column('reporting_date', sa.Date(), nullable=False),
        sa.Column('filing_type', sa.String(length=12), server_default='annual', nullable=False),
        sa.Column('document_path', sa.String(length=600), nullable=False),
        sa.Column('sha256', sa.String(length=64), nullable=False),
        sa.Column('size_bytes', sa.BigInteger(), server_default='0', nullable=False),
        sa.Column('extraction_status', sa.String(length=12), server_default='pending', nullable=False),
        sa.Column('extraction_error', sa.Text(), server_default='', nullable=False),
        sa.Column('notes_text', sa.Text(), server_default='', nullable=False),
        sa.Column('submitted_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('parsed_at', sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('tenant_id', 'account', 'reporting_date', 'filing_type',
                            name='uq_statement_period'),
        schema=SCHEMA,
    )
    op.create_index('ix_statement_tenant_account', 'financial_statements',
                    ['tenant_id', 'account', 'reporting_date'], schema=SCHEMA)
    op.create_index(op.f('ix_lane_c_financial_statements_tenant_id'), 'financial_statements',
                    ['tenant_id'], schema=SCHEMA)
    op.create_index(op.f('ix_lane_c_financial_statements_account'), 'financial_statements',
                    ['account'], schema=SCHEMA)
    op.create_index(op.f('ix_lane_c_financial_statements_sha256'), 'financial_statements',
                    ['sha256'], schema=SCHEMA)
    op.create_index(op.f('ix_lane_c_financial_statements_extraction_status'),
                    'financial_statements', ['extraction_status'], schema=SCHEMA)

    op.create_table(
        'parsed_financials',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('tenant_id', sa.String(length=36), nullable=False),
        sa.Column('statement_id', sa.String(length=36), nullable=False),
        sa.Column('metric_code', sa.String(length=32), nullable=False),
        sa.Column('metric_value_paise', sa.BigInteger(), nullable=False),
        sa.Column('confidence', sa.Float(), server_default='1.0', nullable=False),
        sa.Column('extracted_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['statement_id'], [f'{SCHEMA}.financial_statements.id']),
        sa.PrimaryKeyConstraint('id'),
        schema=SCHEMA,
    )
    op.create_index('ix_parsed_statement', 'parsed_financials',
                    ['statement_id', 'metric_code'], schema=SCHEMA)
    op.create_index(op.f('ix_lane_c_parsed_financials_tenant_id'), 'parsed_financials',
                    ['tenant_id'], schema=SCHEMA)

    op.create_table(
        'computed_signals',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('tenant_id', sa.String(length=36), nullable=False),
        sa.Column('account', sa.String(length=40), nullable=False),
        sa.Column('reporting_date', sa.Date(), nullable=False),
        sa.Column('signal_code', sa.String(length=12), nullable=False),
        sa.Column('observed_value', sa.Float(), nullable=False),
        sa.Column('baseline_value', sa.Float(), nullable=True),
        sa.Column('peer_median', sa.Float(), nullable=True),
        sa.Column('status', sa.String(length=10), nullable=False),
        sa.Column('severity', sa.Integer(), server_default='0', nullable=False),
        sa.Column('evidence', sa.Text(), server_default='', nullable=False),
        sa.Column('evidence_basis', sa.String(length=16), server_default='ratio', nullable=False),
        sa.Column('computed_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('tenant_id', 'account', 'reporting_date', 'signal_code',
                            name='uq_signal_period'),
        schema=SCHEMA,
    )
    op.create_index('ix_signal_tenant_account', 'computed_signals',
                    ['tenant_id', 'account', 'reporting_date'], schema=SCHEMA)

    op.create_table(
        'credit_health_scores',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('tenant_id', sa.String(length=36), nullable=False),
        sa.Column('account', sa.String(length=40), nullable=False),
        sa.Column('reporting_date', sa.Date(), nullable=False),
        sa.Column('score_value', sa.Integer(), nullable=False),
        sa.Column('trend', sa.String(length=14), server_default='new', nullable=False),
        sa.Column('signal_count', sa.Integer(), server_default='0', nullable=False),
        sa.Column('critical_count', sa.Integer(), server_default='0', nullable=False),
        sa.Column('recommendation', sa.String(length=12), nullable=False),
        sa.Column('scored_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('tenant_id', 'account', 'reporting_date', name='uq_score_period'),
        schema=SCHEMA,
    )
    op.create_index(op.f('ix_lane_c_credit_health_scores_tenant_id'), 'credit_health_scores',
                    ['tenant_id'], schema=SCHEMA)
    op.create_index(op.f('ix_lane_c_credit_health_scores_account'), 'credit_health_scores',
                    ['account'], schema=SCHEMA)

    op.create_table(
        'alerts',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('tenant_id', sa.String(length=36), nullable=False),
        sa.Column('account', sa.String(length=40), nullable=False),
        sa.Column('reporting_date', sa.Date(), nullable=False),
        sa.Column('signal_id', sa.String(length=36), nullable=False),
        sa.Column('signal_code', sa.String(length=12), nullable=False),
        sa.Column('severity', sa.String(length=10), nullable=False),
        sa.Column('status', sa.String(length=12), server_default='new', nullable=False),
        sa.Column('assigned_to', sa.String(length=128), server_default='', nullable=False),
        sa.Column('investigation_notes', sa.Text(), server_default='', nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('closed_at', sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(['signal_id'], [f'{SCHEMA}.computed_signals.id']),
        sa.PrimaryKeyConstraint('id'),
        schema=SCHEMA,
    )
    op.create_index('ix_lane_c_alert_tenant_state', 'alerts', ['tenant_id', 'status'],
                    schema=SCHEMA)
    op.create_index(op.f('ix_lane_c_alerts_account'), 'alerts', ['account'], schema=SCHEMA)
    op.create_index(op.f('ix_lane_c_alerts_signal_id'), 'alerts', ['signal_id'], schema=SCHEMA)


def downgrade() -> None:
    op.drop_table('alerts', schema=SCHEMA)
    op.drop_table('credit_health_scores', schema=SCHEMA)
    op.drop_table('computed_signals', schema=SCHEMA)
    op.drop_table('parsed_financials', schema=SCHEMA)
    op.drop_table('financial_statements', schema=SCHEMA)
    op.execute(f'DROP SCHEMA IF EXISTS "{SCHEMA}" RESTRICT')
