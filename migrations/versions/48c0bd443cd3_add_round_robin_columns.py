"""add_round_robin_columns

Adds two nullable columns to the bet table to support Round Robin
parlay submissions from the unified bet slip:

- round_robin_size (Integer, nullable): number of legs per RR combination.
  NULL for non-RR bets. 2 for a 2-team RR, 3 for a 3-team RR, etc.
- parlay_group_id (String 40, nullable): UUID shared by all bet rows
  created in the same RR slip submission, equivalent to parlay_id for
  standard parlays. Indexed for O(1) grouping queries.

Revision ID: 48c0bd443cd3
Revises: a4b5c6d7e8f9
Create Date: 2026-06-27 01:50:11.476577

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '48c0bd443cd3'
down_revision = 'a4b5c6d7e8f9'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('bet', sa.Column('round_robin_size', sa.Integer(), nullable=True))
    op.add_column('bet', sa.Column('parlay_group_id', sa.String(length=40), nullable=True))
    op.create_index('ix_bet_parlay_group_id', 'bet', ['parlay_group_id'])


def downgrade():
    op.drop_index('ix_bet_parlay_group_id', table_name='bet')
    op.drop_column('bet', 'parlay_group_id')
    op.drop_column('bet', 'round_robin_size')
