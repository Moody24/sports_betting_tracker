"""Add shared basketball feed state and bounded replay events.

Revision ID: ea7026bd109c
Revises: d93f1a7c2b40
"""

import sqlalchemy as sa
from alembic import op

revision = 'ea7026bd109c'
down_revision = 'd93f1a7c2b40'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'basketball_feed_state',
        sa.Column('key', sa.String(40), primary_key=True),
        sa.Column('revision', sa.Integer(), nullable=False),
        sa.Column('payload_json', sa.Text(), nullable=False),
        sa.Column('source', sa.String(40), nullable=False),
        sa.Column('observed_at', sa.DateTime(timezone=True)),
        sa.Column('last_success_at', sa.DateTime(timezone=True)),
        sa.Column('last_attempt_at', sa.DateTime(timezone=True)),
        sa.Column('error_code', sa.String(40)),
        sa.Column('retry_at', sa.DateTime(timezone=True)),
        sa.Column('failure_count', sa.Integer(), nullable=False),
    )
    op.create_table(
        'basketball_feed_event',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('key', sa.String(40), nullable=False),
        sa.Column('payload_json', sa.Text(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sqlite_autoincrement=True,
    )
    op.create_index('ix_basketball_feed_event_key', 'basketball_feed_event', ['key'])


def downgrade():
    op.drop_index('ix_basketball_feed_event_key', table_name='basketball_feed_event')
    op.drop_table('basketball_feed_event')
    op.drop_table('basketball_feed_state')
