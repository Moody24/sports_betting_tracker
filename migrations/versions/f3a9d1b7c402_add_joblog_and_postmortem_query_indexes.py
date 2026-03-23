"""add_joblog_and_postmortem_query_indexes

Adds two indexes identified by the Observability health-report command:

1. job_log.started_at — health-report filters jobs by rolling time window
   (e.g. last 7 days).  Without an index every query scans the full table.
   At 17 job types × ~4 runs/day the table grows ~60 rows/day; the index
   pays for itself within weeks.

2. bet_postmortem(stat_type, created_at) — the drift-analysis section of
   health-report aggregates errors by stat_type within a cutoff window.
   The existing single-column indexes on player_name / game_date / primary_reason
   don't cover this access pattern.

Revision ID: f3a9d1b7c402
Revises: deef0459a5af
Create Date: 2026-03-22 21:15:00.000000
"""
from alembic import op


# revision identifiers, used by Alembic.
revision = 'f3a9d1b7c402'
down_revision = 'deef0459a5af'
branch_labels = None
depends_on = None


def upgrade():
    # Scheduler-health query: WHERE started_at >= cutoff ORDER BY started_at DESC
    op.create_index(
        'ix_job_log_started_at',
        'job_log',
        ['started_at'],
    )
    # Drift-analysis query: WHERE created_at >= cutoff GROUP BY stat_type
    # Leading column is created_at (range predicate); stat_type follows for
    # grouping.  Covering index avoids a heap fetch for the common case.
    op.create_index(
        'ix_bet_postmortem_created_at_stat_type',
        'bet_postmortem',
        ['created_at', 'stat_type'],
    )


def downgrade():
    op.drop_index('ix_bet_postmortem_created_at_stat_type', table_name='bet_postmortem')
    op.drop_index('ix_job_log_started_at', table_name='job_log')
