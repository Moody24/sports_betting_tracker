"""drop_context_flags

Remove the unused PlayerGameLog.context_flags column.  The column has been
written by stats_service but never read by any ML training or inference path.

Revision ID: a9b8c7d6e5f4
Revises: f1a2b3c4d5e6
Create Date: 2026-03-11 00:01:00.000000
"""
import sqlalchemy as sa
from alembic import op


revision = 'a9b8c7d6e5f4'
down_revision = 'f1a2b3c4d5e6'
branch_labels = None
depends_on = None


def upgrade():
    op.drop_column('player_game_log', 'context_flags')


def downgrade():
    op.add_column(
        'player_game_log',
        sa.Column('context_flags', sa.Text(), nullable=True),
    )
