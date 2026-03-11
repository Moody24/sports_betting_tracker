"""add_stat_analysis_indexes

Revision ID: cdfad111ad37
Revises: 37824fa75dbc
Create Date: 2026-03-09 08:56:10.263075

"""
from alembic import op


# revision identifiers, used by Alembic.
revision = 'cdfad111ad37'
down_revision = '37824fa75dbc'
branch_labels = None
depends_on = None


def upgrade():
    # Speeds up _hit_rates_from_logs player lookups (was table-scanning)
    op.create_index(
        'ix_player_game_log_player_name',
        'player_game_log', ['player_name'],
        unique=False,
    )
    # Speeds up _build_stat_context team defense lookups (was table-scanning)
    op.create_index(
        'ix_team_defense_snapshot_team_abbr',
        'team_defense_snapshot', ['team_abbr'],
        unique=False,
    )


def downgrade():
    op.drop_index('ix_team_defense_snapshot_team_abbr', table_name='team_defense_snapshot')
    op.drop_index('ix_player_game_log_player_name', table_name='player_game_log')
