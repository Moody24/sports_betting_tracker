"""add_bet_table_indexes

Revision ID: c3a1f7e82b04
Revises: b6f9fdecc99a
Create Date: 2026-03-03 22:00:00.000000

"""
from alembic import op


# revision identifiers, used by Alembic.
revision = 'c3a1f7e82b04'
down_revision = 'b6f9fdecc99a'
branch_labels = None
depends_on = None


def upgrade():
    op.create_index('ix_bet_user_id', 'bet', ['user_id'])
    op.create_index('ix_bet_match_date', 'bet', ['match_date'])
    op.create_index('ix_bet_outcome', 'bet', ['outcome'])
    op.create_index('ix_bet_parlay_id', 'bet', ['parlay_id'])


def downgrade():
    op.drop_index('ix_bet_parlay_id', table_name='bet')
    op.drop_index('ix_bet_outcome', table_name='bet')
    op.drop_index('ix_bet_match_date', table_name='bet')
    op.drop_index('ix_bet_user_id', table_name='bet')
