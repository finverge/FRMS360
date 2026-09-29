"""tenants.is_sandbox - BR-109 self-service sandbox tenants.

Set by POST /tenants/self-service, cleared only by the deliberate platform_admin
promotion action (POST /tenants/{id}/promote). Ingestion refuses a sandbox tenant's
batch that claims source="live" - see services/ingestion_service/app/routes/ingest.py.

Revision ID: 1eeb6f553d3e
Revises: 034ddfa06f53
Create Date: 2026-08-18 08:10:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '1eeb6f553d3e'
down_revision: Union[str, None] = '034ddfa06f53'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('tenants', sa.Column('is_sandbox', sa.Boolean(), server_default='false',
                                       nullable=False), schema='tenant')


def downgrade() -> None:
    op.drop_column('tenants', 'is_sandbox', schema='tenant')
