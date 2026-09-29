"""ofac sanctions screening data

Real OFAC SDN list (US Treasury's public Specially Designated Nationals list) plus its
alternate-names file, loaded and refreshed by scripts/load_ofac_sdn.py - not synthetic
data. pg_trgm backs fuzzy name screening (screen a typed name against sdn_name/alt_name
by trigram similarity), the same technique a real sanctions-screening tool uses, since
OFAC names rarely match a query string exactly.

Revision ID: d743302a5c81
Revises: 25bc2fecde48
Create Date: 2026-09-28 17:38:01.245834

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd743302a5c81'
down_revision: Union[str, None] = '25bc2fecde48'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")

    op.create_table(
        'ofac_sdn_entry',
        sa.Column('ent_num', sa.Integer(), nullable=False),
        sa.Column('sdn_name', sa.String(length=350), nullable=False),
        sa.Column('sdn_type', sa.String(length=50), server_default='', nullable=False),
        sa.Column('program', sa.String(length=200), server_default='', nullable=False),
        sa.Column('remarks', sa.Text(), server_default='', nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('ent_num'),
        schema='analytics',
    )
    op.execute(
        "CREATE INDEX ix_ofac_sdn_entry_name_trgm ON analytics.ofac_sdn_entry "
        "USING GIN (sdn_name gin_trgm_ops)"
    )

    op.create_table(
        'ofac_sdn_alias',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('ent_num', sa.Integer(), nullable=False),
        sa.Column('alt_type', sa.String(length=20), server_default='', nullable=False),
        sa.Column('alt_name', sa.String(length=350), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['ent_num'], ['analytics.ofac_sdn_entry.ent_num'], ondelete='CASCADE'),
        schema='analytics',
    )
    op.create_index(
        op.f('ix_analytics_ofac_sdn_alias_ent_num'), 'ofac_sdn_alias', ['ent_num'],
        unique=False, schema='analytics',
    )
    op.execute(
        "CREATE INDEX ix_ofac_sdn_alias_name_trgm ON analytics.ofac_sdn_alias "
        "USING GIN (alt_name gin_trgm_ops)"
    )


def downgrade() -> None:
    op.drop_table('ofac_sdn_alias', schema='analytics')
    op.drop_table('ofac_sdn_entry', schema='analytics')
