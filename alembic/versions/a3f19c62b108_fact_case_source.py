"""fact_case.source — provenance on the case, not only on its evidence (BR-611)

fact_transaction and fact_alert already carry ``source``; fact_case did not, so the one
entity a regulatory return is actually filed against was the one entity whose provenance
could not be checked.

The server default is ``synthetic`` while the model default is ``live``. That asymmetry is
deliberate and matches the two fact tables: rows that already exist were written before
provenance was tracked, and on this installation they are overwhelmingly the demonstration
corpus, so defaulting them to ``live`` would silently bless 99% of the data as real. New
rows come from the application, which sets the value explicitly.

Existing rows are then corrected from their evidence where it is knowable: a case whose
alerts are all live is live. A case with no alerts stays ``synthetic``, because a case
whose provenance cannot be established is exactly the one that must not back a filing.

Revision ID: a3f19c62b108
Revises: 7dfd2035ef75
"""
from alembic import op
import sqlalchemy as sa

revision = "a3f19c62b108"
down_revision = "7dfd2035ef75"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "fact_case",
        sa.Column("source", sa.String(length=16), nullable=False,
                  server_default="synthetic"),
        schema="analytics",
    )
    op.create_index("ix_analytics_fact_case_source", "fact_case", ["source"],
                    schema="analytics")

    # Promote to live only where every linked alert is live. A case mixing live and
    # demonstration evidence is not live - it is contaminated, and stays flagged.
    op.execute("""
        UPDATE analytics.fact_case c
           SET source = 'live'
         WHERE EXISTS (SELECT 1 FROM analytics.fact_alert a
                        WHERE a.case_id = c.case_id AND a.tenant_id = c.tenant_id)
           AND NOT EXISTS (SELECT 1 FROM analytics.fact_alert a
                            WHERE a.case_id = c.case_id AND a.tenant_id = c.tenant_id
                              AND a.source <> 'live')
    """)


def downgrade() -> None:
    op.drop_index("ix_analytics_fact_case_source", table_name="fact_case",
                  schema="analytics")
    op.drop_column("fact_case", "source", schema="analytics")
