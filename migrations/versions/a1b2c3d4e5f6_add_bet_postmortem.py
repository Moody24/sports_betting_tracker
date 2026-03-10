"""add_bet_postmortem

Adds the bet_postmortem table that stores structured postmortem analysis for
each settled player-prop bet leg.  One row per settled bet (enforced by the
unique constraint on bet_id).

Revision ID: a1b2c3d4e5f6
Revises: e7a3f1c9b204
Create Date: 2026-03-10 00:00:00.000000
"""
import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision = 'a1b2c3d4e5f6'
down_revision = 'e7a3f1c9b204'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'bet_postmortem',
        sa.Column('id', sa.Integer, primary_key=True),

        # Foreign key — unique so at most one postmortem per bet
        sa.Column(
            'bet_id',
            sa.Integer,
            sa.ForeignKey('bet.id', ondelete='CASCADE'),
            nullable=False,
            unique=True,
        ),

        # Denormalised bet metadata (avoids joins in reporting queries)
        sa.Column('player_name', sa.String(100), nullable=True),
        sa.Column('game_date', sa.Date, nullable=True),
        sa.Column('stat_type', sa.String(40), nullable=True),
        sa.Column('bet_side', sa.String(10), nullable=True),
        sa.Column('prop_line', sa.Float, nullable=True),

        # Pregame expectations
        sa.Column('projected_stat', sa.Float, nullable=True),
        sa.Column('expected_minutes', sa.Float, nullable=True),
        sa.Column('expected_attempts', sa.Float, nullable=True),
        sa.Column('expected_pace', sa.Float, nullable=True),

        # Postgame reality
        sa.Column('actual_stat', sa.Float, nullable=True),
        sa.Column('actual_minutes', sa.Float, nullable=True),
        sa.Column('actual_attempts', sa.Float, nullable=True),

        # Diagnostic deltas
        sa.Column('projection_error', sa.Float, nullable=True),
        sa.Column('miss_margin', sa.Float, nullable=True),
        sa.Column('minutes_delta', sa.Float, nullable=True),
        sa.Column('attempts_delta', sa.Float, nullable=True),

        # Game-context flags
        sa.Column('overtime_flag', sa.Boolean, nullable=False, server_default='0'),
        sa.Column('blowout_flag', sa.Boolean, nullable=False, server_default='0'),

        # Reason codes (PostmortemReason enum values stored as strings)
        sa.Column('primary_reason_code', sa.String(40), nullable=True),
        sa.Column('secondary_reason_code', sa.String(40), nullable=True),
        sa.Column('tertiary_reason_code', sa.String(40), nullable=True),
        sa.Column('reason_confidence', sa.Float, nullable=True),

        # Full diagnostic payload
        sa.Column('diagnosis_json', sa.Text, nullable=True),

        # Timestamps
        sa.Column('created_at', sa.DateTime, nullable=False),
        sa.Column('updated_at', sa.DateTime, nullable=False),
    )

    # Indexes for the most common query patterns
    op.create_index('ix_bet_postmortem_bet_id', 'bet_postmortem', ['bet_id'])
    op.create_index('ix_bet_postmortem_player_name', 'bet_postmortem', ['player_name'])
    op.create_index('ix_bet_postmortem_game_date', 'bet_postmortem', ['game_date'])
    op.create_index(
        'ix_bet_postmortem_primary_reason',
        'bet_postmortem',
        ['primary_reason_code'],
    )


def downgrade():
    op.drop_index('ix_bet_postmortem_primary_reason', table_name='bet_postmortem')
    op.drop_index('ix_bet_postmortem_game_date', table_name='bet_postmortem')
    op.drop_index('ix_bet_postmortem_player_name', table_name='bet_postmortem')
    op.drop_index('ix_bet_postmortem_bet_id', table_name='bet_postmortem')
    op.drop_table('bet_postmortem')
