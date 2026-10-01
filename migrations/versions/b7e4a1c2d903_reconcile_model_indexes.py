"""Align existing database indexes with the ORM schema.

Revision ID: b7e4a1c2d903
Revises: a6d4c2e8f901
"""

import sqlalchemy as sa
from alembic import op


revision = 'b7e4a1c2d903'
down_revision = 'a6d4c2e8f901'
branch_labels = None
depends_on = None


_EXPECTED_INDEXES = (
    ('bet_postmortem', 'ix_bet_postmortem_bet_id', ('bet_id',)),
    ('bet_postmortem', 'ix_bet_postmortem_created_at_stat_type',
     ('created_at', 'stat_type')),
    ('bet_postmortem', 'ix_bet_postmortem_primary_reason',
     ('primary_reason_code',)),
    ('game_snapshot', 'ix_game_snapshot_espn_id_game_date',
     ('espn_id', 'game_date')),
    ('job_log', 'ix_job_log_started_at', ('started_at',)),
    ('model_metadata', 'ix_model_metadata_model_name_is_active',
     ('model_name', 'is_active')),
    ('player_game_log', 'ix_player_game_log_player_name', ('player_name',)),
    ('team_defense_snapshot', 'ix_team_defense_snapshot_team_name',
     ('team_name',)),
)


def _indexes(table):
    return {index['name'] for index in sa.inspect(op.get_bind()).get_indexes(table)}


def upgrade():
    old_name = 'ix_bet_postmortem_primary_reason_code'
    if old_name in _indexes('bet_postmortem'):
        op.drop_index(old_name, table_name='bet_postmortem')
    for table, name, columns in _EXPECTED_INDEXES:
        if name not in _indexes(table):
            op.create_index(name, table, list(columns))


def downgrade():
    # Legacy databases reached this revision with different subsets of the
    # expected indexes. Leave them in place on rollback; dropping an index
    # that existed before this migration would make that schema worse.
    old_name = 'ix_bet_postmortem_primary_reason_code'
    if old_name not in _indexes('bet_postmortem'):
        op.create_index(old_name, 'bet_postmortem', ['primary_reason_code'])
