"""decision schema — the inline decision lane (Lane A)

Two tables in their own schema, owned by a role that can reach nothing else. A service
that sits in the payment path must not be able to run an expensive query by accident, and
the cheapest way to guarantee that is to deny it the tables entirely.

``account_counters`` is written on the ingestion path and only ever read on the decision
path. The unique constraint is on (tenant, account, observation) so a counter update is a
single upsert rather than a read-modify-write.

Revision ID: c8b41d0e97aa
Revises: a3f19c62b108
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "c8b41d0e97aa"
down_revision = "a3f19c62b108"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE SCHEMA IF NOT EXISTS decision")

    op.create_table(
        "account_counters",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("tenant_id", sa.String(length=36), nullable=False),
        sa.Column("account", sa.String(length=64), nullable=False),
        sa.Column("observation", sa.String(length=48), nullable=False),
        sa.Column("value", sa.Float(), nullable=False, server_default="0"),
        sa.Column("as_of", sa.DateTime(timezone=True), server_default=sa.func.now(),
                  nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(),
                  nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "account", "observation",
                            name="uq_counter_per_account_observation"),
        schema="decision",
    )
    op.create_index("ix_decision_account_counters_tenant_id", "account_counters",
                    ["tenant_id"], schema="decision")
    op.create_index("ix_counter_lookup", "account_counters", ["tenant_id", "account"],
                    schema="decision")

    op.create_table(
        "decision_log",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("tenant_id", sa.String(length=36), nullable=False),
        sa.Column("txn_ref", sa.String(length=64), nullable=False),
        sa.Column("rail", sa.String(length=8), nullable=False),
        sa.Column("debtor_account", sa.String(length=64), nullable=False),
        sa.Column("creditor_account", sa.String(length=64), nullable=False,
                  server_default=""),
        sa.Column("amount_paise", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("action", sa.String(length=12), nullable=False),
        sa.Column("outcome", sa.String(length=20), nullable=False),
        sa.Column("enforced", sa.Boolean(), nullable=False, server_default=sa.false()),
        # NOT NULL with an empty-list default: "nothing matched" and "nothing was
        # skipped" are facts worth recording, and NULL would make them indistinguishable
        # from a decision that never populated them.
        sa.Column("matched", postgresql.JSONB(astext_type=sa.Text()), nullable=False,
                  server_default=sa.text("'[]'::jsonb")),
        sa.Column("skipped", postgresql.JSONB(astext_type=sa.Text()), nullable=False,
                  server_default=sa.text("'[]'::jsonb")),
        sa.Column("took_ms", sa.Float(), nullable=False, server_default="0"),
        sa.Column("lookup_ms", sa.Float(), nullable=False, server_default="0"),
        sa.Column("evaluate_ms", sa.Float(), nullable=False, server_default="0"),
        sa.Column("budget_ms", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("policy_version", sa.String(length=24), nullable=False,
                  server_default=""),
        sa.Column("decided_at", sa.DateTime(timezone=True), server_default=sa.func.now(),
                  nullable=False),
        sa.Column("error", sa.Text(), nullable=False, server_default=""),
        sa.PrimaryKeyConstraint("id"),
        schema="decision",
    )
    for col in ("tenant_id", "rail", "action", "outcome", "enforced"):
        op.create_index(f"ix_decision_decision_log_{col}", "decision_log", [col],
                        schema="decision")
    op.create_index("ix_decision_tenant_ts", "decision_log", ["tenant_id", "decided_at"],
                    schema="decision")
    op.create_index("ix_decision_txn", "decision_log", ["tenant_id", "txn_ref"],
                    schema="decision")


def downgrade() -> None:
    op.drop_table("decision_log", schema="decision")
    op.drop_table("account_counters", schema="decision")
    op.execute("DROP SCHEMA IF EXISTS decision")
