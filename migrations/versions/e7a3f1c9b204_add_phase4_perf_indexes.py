"""add_phase4_perf_indexes

Add indexes on hot query columns that were identified during Phase 4 of the
performance hardening pass:
  - injury_report.player_name        (ilike lookup per player in scoring loop)
  - injury_report.date_reported      (filter_by + ORDER BY in refresh_injuries)
  - team_defense_snapshot.snapshot_date (ORDER BY in get_team_defense ilike query)
  - game_snapshot.game_date          (filter in nba_today + completed_snaps query)
  - composite (game_snapshot.espn_id, game_snapshot.game_date) for the exact
    lookup in nba_today and nba_props routes

Revision ID: e7a3f1c9b204
Revises: cdfad111ad37
Create Date: 2026-03-09 23:50:00.000000
"""
from alembic import op


# revision identifiers, used by Alembic.
revision = 'e7a3f1c9b204'
down_revision = 'cdfad111ad37'
branch_labels = None
depends_on = None


def upgrade():
    op.create_index(
        'ix_injury_report_player_name',
        'injury_report', ['player_name'],
        unique=False,
    )
    op.create_index(
        'ix_injury_report_date_reported',
        'injury_report', ['date_reported'],
        unique=False,
    )
    op.create_index(
        'ix_team_defense_snapshot_snapshot_date',
        'team_defense_snapshot', ['snapshot_date'],
        unique=False,
    )
    op.create_index(
        'ix_game_snapshot_game_date',
        'game_snapshot', ['game_date'],
        unique=False,
    )
    op.create_index(
        'ix_game_snapshot_espn_id_game_date',
        'game_snapshot', ['espn_id', 'game_date'],
        unique=False,
    )


def downgrade():
    op.drop_index('ix_game_snapshot_espn_id_game_date', table_name='game_snapshot')
    op.drop_index('ix_game_snapshot_game_date', table_name='game_snapshot')
    op.drop_index('ix_team_defense_snapshot_snapshot_date', table_name='team_defense_snapshot')
    op.drop_index('ix_injury_report_date_reported', table_name='injury_report')
    op.drop_index('ix_injury_report_player_name', table_name='injury_report')
