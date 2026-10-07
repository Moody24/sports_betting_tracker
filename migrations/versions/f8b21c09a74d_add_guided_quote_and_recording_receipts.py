"""Add current guided quote observations and atomic bet recording receipts.

Revision ID: f8b21c09a74d
Revises: ea7026bd109c
"""

import sqlalchemy as sa
from alembic import op

revision = 'f8b21c09a74d'
down_revision = 'ea7026bd109c'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'guided_quote',
        sa.Column('key', sa.String(64), primary_key=True),
        sa.Column('event_id', sa.String(100), nullable=False),
        sa.Column('payload_json', sa.Text(), nullable=False),
        sa.Column('observed_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('available', sa.Boolean(), nullable=False),
    )
    op.create_index('ix_guided_quote_event_id', 'guided_quote', ['event_id'])
    op.create_index('ix_guided_quote_observed_at', 'guided_quote', ['observed_at'])
    op.create_table(
        'bet_recording_request',
        sa.Column('user_id', sa.Integer(), sa.ForeignKey('user.id', ondelete='CASCADE'), primary_key=True),
        sa.Column('request_id', sa.String(36), primary_key=True),
        sa.Column('payload_hash', sa.String(64), nullable=False),
        sa.Column('request_json', sa.Text(), nullable=False),
        sa.Column('response_json', sa.Text()),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    )


def downgrade():
    op.drop_table('bet_recording_request')
    op.drop_index('ix_guided_quote_observed_at', table_name='guided_quote')
    op.drop_index('ix_guided_quote_event_id', table_name='guided_quote')
    op.drop_table('guided_quote')
