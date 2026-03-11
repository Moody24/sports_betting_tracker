"""add_composite_indexes

Add composite indexes on hot query paths:
  - bet(user_id, outcome)          — dashboard filter (currently uses two separate indexes)
  - player_game_log(player_name, game_date) — feature builder player lookup

Revision ID: f1a2b3c4d5e6
Revises: e7a3f1c9b204
Create Date: 2026-03-11 00:00:00.000000
"""
from alembic import op


revision = 'f1a2b3c4d5e6'
down_revision = 'e7a3f1c9b204'
branch_labels = None
depends_on = None


def upgrade():
    op.create_index(
        'ix_bet_user_outcome',
        'bet', ['user_id', 'outcome'],
        unique=False,
    )
    op.create_index(
        'ix_player_game_log_player_date',
        'player_game_log', ['player_name', 'game_date'],
        unique=False,
    )


def downgrade():
    op.drop_index('ix_player_game_log_player_date', table_name='player_game_log')
    op.drop_index('ix_bet_user_outcome', table_name='bet')
