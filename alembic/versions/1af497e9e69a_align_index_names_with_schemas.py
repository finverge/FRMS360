"""align index names with schemas

ALTER TABLE ... SET SCHEMA carries indexes across but keeps their names. SQLAlchemy derives
the name of an index=True column index from the schema as well as the table, so after the
move every one of them differed from what the models declare - and alembic check, the
drift detector this project relies on, reported 46 phantom changes.

Renaming is instant catalogue work. Autogenerate proposed dropping and recreating all 46,
which on a real transaction table is hours of index rebuild for a cosmetic difference.

Revision ID: 1af497e9e69a
Revises: 110f2828d64b
Create Date: 2026-07-29

"""
from typing import Sequence, Union

from alembic import op

revision: str = '1af497e9e69a'
down_revision: Union[str, None] = '110f2828d64b'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# (schema, old name, new name)
RENAMES = [
    ('analytics', 'ix_fact_alert_case_id', 'ix_analytics_fact_alert_case_id'),
    ('analytics', 'ix_fact_alert_disposition', 'ix_analytics_fact_alert_disposition'),
    ('analytics', 'ix_fact_alert_rule_family', 'ix_analytics_fact_alert_rule_family'),
    ('analytics', 'ix_fact_alert_rule_id', 'ix_analytics_fact_alert_rule_id'),
    ('analytics', 'ix_fact_alert_severity', 'ix_analytics_fact_alert_severity'),
    ('analytics', 'ix_fact_alert_tenant_id', 'ix_analytics_fact_alert_tenant_id'),
    ('analytics', 'ix_fact_alert_ts', 'ix_analytics_fact_alert_ts'),
    ('analytics', 'ix_fact_alert_txn_id', 'ix_analytics_fact_alert_txn_id'),
    ('analytics', 'ix_fact_case_customer_segment', 'ix_analytics_fact_case_customer_segment'),
    ('analytics', 'ix_fact_case_fmr_category', 'ix_analytics_fact_case_fmr_category'),
    ('analytics', 'ix_fact_case_opened_ts', 'ix_analytics_fact_case_opened_ts'),
    ('analytics', 'ix_fact_case_product', 'ix_analytics_fact_case_product'),
    ('analytics', 'ix_fact_case_rail', 'ix_analytics_fact_case_rail'),
    ('analytics', 'ix_fact_case_region', 'ix_analytics_fact_case_region'),
    ('analytics', 'ix_fact_case_severity', 'ix_analytics_fact_case_severity'),
    ('analytics', 'ix_fact_case_state', 'ix_analytics_fact_case_state'),
    ('analytics', 'ix_fact_case_tenant_id', 'ix_analytics_fact_case_tenant_id'),
    ('analytics', 'ix_fact_transaction_creditor_account', 'ix_analytics_fact_transaction_creditor_account'),
    ('analytics', 'ix_fact_transaction_customer_segment', 'ix_analytics_fact_transaction_customer_segment'),
    ('analytics', 'ix_fact_transaction_debtor_account', 'ix_analytics_fact_transaction_debtor_account'),
    ('analytics', 'ix_fact_transaction_product', 'ix_analytics_fact_transaction_product'),
    ('analytics', 'ix_fact_transaction_rail', 'ix_analytics_fact_transaction_rail'),
    ('analytics', 'ix_fact_transaction_region', 'ix_analytics_fact_transaction_region'),
    ('analytics', 'ix_fact_transaction_tenant_id', 'ix_analytics_fact_transaction_tenant_id'),
    ('analytics', 'ix_fact_transaction_ts', 'ix_analytics_fact_transaction_ts'),
    ('analytics', 'ix_report_subscriptions_owner', 'ix_analytics_report_subscriptions_owner'),
    ('analytics', 'ix_report_subscriptions_tenant_id', 'ix_analytics_report_subscriptions_tenant_id'),
    ('analytics', 'ix_saved_views_owner', 'ix_analytics_saved_views_owner'),
    ('analytics', 'ix_saved_views_tenant_id', 'ix_analytics_saved_views_tenant_id'),
    ('cases', 'ix_case_documents_case_id', 'ix_cases_case_documents_case_id'),
    ('cases', 'ix_case_documents_doc_type', 'ix_cases_case_documents_doc_type'),
    ('cases', 'ix_case_documents_tenant_id', 'ix_cases_case_documents_tenant_id'),
    ('cases', 'ix_case_transitions_actor', 'ix_cases_case_transitions_actor'),
    ('cases', 'ix_case_transitions_case_id', 'ix_cases_case_transitions_case_id'),
    ('cases', 'ix_case_transitions_created_at', 'ix_cases_case_transitions_created_at'),
    ('cases', 'ix_case_transitions_status', 'ix_cases_case_transitions_status'),
    ('cases', 'ix_case_transitions_tenant_id', 'ix_cases_case_transitions_tenant_id'),
    ('config', 'ix_tenant_configs_tenant_id', 'ix_config_tenant_configs_tenant_id'),
    ('platform', 'ix_audit_logs_action', 'ix_platform_audit_logs_action'),
    ('platform', 'ix_audit_logs_service', 'ix_platform_audit_logs_service'),
    ('platform', 'ix_audit_logs_tenant_id', 'ix_platform_audit_logs_tenant_id'),
    ('platform', 'ix_audit_logs_ts', 'ix_platform_audit_logs_ts'),
    ('tenant', 'ix_platform_users_email', 'ix_tenant_platform_users_email'),
    ('tenant', 'ix_tenant_users_email', 'ix_tenant_tenant_users_email'),
    ('tenant', 'ix_tenant_users_tenant_id', 'ix_tenant_tenant_users_tenant_id'),
    ('tenant', 'ix_tenants_slug', 'ix_tenant_tenants_slug'),
]


def _rename(schema: str, src: str, dst: str) -> None:
    # Guarded so this is re-runnable, and tolerates a database built fresh from the
    # models where the index already carries its final name.
    op.execute(
        "DO $$ BEGIN "
        "IF EXISTS (SELECT 1 FROM pg_indexes WHERE schemaname = '" + schema + "' "
        "AND indexname = '" + src + "') THEN "
        'ALTER INDEX "' + schema + '"."' + src + '" RENAME TO "' + dst + '"; '
        "END IF; END $$;")


def upgrade() -> None:
    for schema, old, new in RENAMES:
        _rename(schema, old, new)


def downgrade() -> None:
    for schema, old, new in RENAMES:
        _rename(schema, new, old)
