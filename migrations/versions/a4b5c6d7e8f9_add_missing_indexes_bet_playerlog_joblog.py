"""add_missing_indexes_bet_playerlog_joblog

Add four indexes identified as missing during Phase 4 performance hardening:

1. bet(user_id, created_at)        — composite for dashboard sort (user bets
                                      ordered by creation time, avoids full
                                      table scan on user_id then filesort)
2. bet(external_game_id)           — live grading lookup; grading loop filters
                                      all open bets by external_game_id to match
                                      completed games
3. player_game_log(cache_expires)  — prune scheduler finds expired cache rows;
                                      without this index a full scan is needed
                                      on each prune cycle
4. job_log(job_name)               — observability queries filter/aggregate by
                                      job_name; existing indexes don't cover
                                      this access pattern

Revision ID: a4b5c6d7e8f9
Revises: f3a9d1b7c402
Create Date: 2026-06-25 12:55:00.000000
"""
from alembic import op


# revision identifiers, used by Alembic.
revision = 'a4b5c6d7e8f9'
down_revision = 'f3a9d1b7c402'
branch_labels = None
depends_on = None


def upgrade():
    # Composite index for dashboard sort: bets by user ordered by creation time
    op.create_index('ix_bet_user_created_at', 'bet', ['user_id', 'created_at'], unique=False)

    # Index for live grading lookup by external game ID
    op.create_index('ix_bet_external_game_id', 'bet', ['external_game_id'], unique=False)

    # Index for prune job: find expired player game log cache entries
    op.create_index('ix_player_game_log_cache_expires', 'player_game_log', ['cache_expires'], unique=False)

    # Index for observability queries on job name
    op.create_index('ix_job_log_job_name', 'job_log', ['job_name'], unique=False)


def downgrade():
    op.drop_index('ix_job_log_job_name', table_name='job_log')
    op.drop_index('ix_player_game_log_cache_expires', table_name='player_game_log')
    op.drop_index('ix_bet_external_game_id', table_name='bet')
    op.drop_index('ix_bet_user_created_at', table_name='bet')
