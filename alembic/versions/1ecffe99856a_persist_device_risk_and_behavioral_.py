"""persist device risk and behavioral biometric scores on fact_transaction

Closes a gap that was already registered, not a new idea: adapters.py's _common()
has accepted device_risk_score/behavior_anomaly_score on any rail payload since the
AI/ML roadmap Phase 1 work (BRD OD-06/S19), and cp_common.observations.observe()
already turns them into CHN-04/CHN-05 - but detection/engine.py's projection step
never carried either field onto fact_transaction, so a score that arrived, scored, and
even fired an alert was gone from the record afterward: nothing to show on the
transaction itself once its own scoring pass had finished with it. NEEDS_EXTERNAL_DATA
already explains why CHN-04/CHN-05 read "unmeasurable" today (no tenant has VideoPD or
Human Fraud Detection Framework wired in) - this migration doesn't change that; it just
stops throwing the score away on the rare batch that does carry one, so it can be
inspected on the transaction (see the new "Behavioral Biometrics" evidence-drawer tab)
rather than living only in whichever alert happened to cross the 0.7 threshold.

Revision ID: 1ecffe99856a
Revises: d743302a5c81
Create Date: 2026-09-28 21:57:08.846069

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '1ecffe99856a'
down_revision: Union[str, None] = 'd743302a5c81'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('fact_transaction', sa.Column('device_risk_score', sa.Float(), nullable=True),
                  schema='analytics')
    op.add_column('fact_transaction', sa.Column('behavior_anomaly_score', sa.Float(), nullable=True),
                  schema='analytics')


def downgrade() -> None:
    op.drop_column('fact_transaction', 'behavior_anomaly_score', schema='analytics')
    op.drop_column('fact_transaction', 'device_risk_score', schema='analytics')
