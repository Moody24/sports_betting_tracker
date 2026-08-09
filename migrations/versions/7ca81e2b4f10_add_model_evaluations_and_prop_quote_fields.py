"""add model evaluations and player-prop quote fields

Revision ID: 7ca81e2b4f10
Revises: 21dc7a4c6b61
Create Date: 2026-08-09 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = '7ca81e2b4f10'
down_revision = '21dc7a4c6b61'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('odds_snapshots') as batch_op:
        batch_op.add_column(sa.Column('source_event_id', sa.String(length=64), nullable=True))
        batch_op.add_column(sa.Column('event_start_time', sa.DateTime(timezone=True), nullable=True))
        batch_op.add_column(sa.Column('player_id', sa.String(length=20), nullable=True))
        batch_op.add_column(sa.Column('player_key', sa.String(length=140), nullable=True))
        batch_op.add_column(sa.Column('source', sa.String(length=30), nullable=False,
                                      server_default='odds_api'))
        batch_op.add_column(sa.Column('snapshot_kind', sa.String(length=20), nullable=False,
                                      server_default='scheduled'))
        batch_op.add_column(sa.Column('source_snapshot_key', sa.String(length=160), nullable=True))
        batch_op.create_index('ix_odds_snapshots_source_event_id', ['source_event_id'])
        batch_op.create_index('ix_odds_snapshots_event_start_time', ['event_start_time'])
        batch_op.create_index('ix_odds_snapshots_player_id', ['player_id'])
        batch_op.create_index('ix_odds_snapshots_player_key', ['player_key'])
        batch_op.create_index(
            'ix_odds_snap_backtest',
            ['market', 'event_start_time', 'player_key', 'bookmaker', 'snapped_at'],
        )
        batch_op.create_unique_constraint(
            'uq_odds_snapshot_source_key', ['source_snapshot_key'],
        )

    op.create_table(
        'model_evaluation_run',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('evaluation_type', sa.String(length=40), nullable=False),
        sa.Column('model_name', sa.String(length=80), nullable=True),
        sa.Column('model_version', sa.String(length=80), nullable=True),
        sa.Column('stat_type', sa.String(length=60), nullable=True),
        sa.Column('started_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('finished_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('status', sa.String(length=20), nullable=False),
        sa.Column('verdict', sa.String(length=20), nullable=True),
        sa.Column('config_json', sa.Text(), nullable=True),
        sa.Column('metrics_json', sa.Text(), nullable=True),
        sa.Column('artifact_path', sa.String(length=300), nullable=True),
        sa.Column('code_revision', sa.String(length=64), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_model_evaluation_run_evaluation_type', 'model_evaluation_run',
                    ['evaluation_type'])
    op.create_index('ix_model_evaluation_run_model_name', 'model_evaluation_run',
                    ['model_name'])
    op.create_index('ix_model_evaluation_run_stat_type', 'model_evaluation_run', ['stat_type'])
    op.create_index('ix_model_evaluation_run_status', 'model_evaluation_run', ['status'])
    op.create_index('ix_model_eval_type_stat_created', 'model_evaluation_run',
                    ['evaluation_type', 'stat_type', 'created_at'])


def downgrade():
    op.drop_index('ix_model_eval_type_stat_created', table_name='model_evaluation_run')
    op.drop_index('ix_model_evaluation_run_status', table_name='model_evaluation_run')
    op.drop_index('ix_model_evaluation_run_stat_type', table_name='model_evaluation_run')
    op.drop_index('ix_model_evaluation_run_model_name', table_name='model_evaluation_run')
    op.drop_index('ix_model_evaluation_run_evaluation_type', table_name='model_evaluation_run')
    op.drop_table('model_evaluation_run')

    with op.batch_alter_table('odds_snapshots') as batch_op:
        batch_op.drop_constraint('uq_odds_snapshot_source_key', type_='unique')
        batch_op.drop_index('ix_odds_snap_backtest')
        batch_op.drop_index('ix_odds_snapshots_player_key')
        batch_op.drop_index('ix_odds_snapshots_player_id')
        batch_op.drop_index('ix_odds_snapshots_event_start_time')
        batch_op.drop_index('ix_odds_snapshots_source_event_id')
        batch_op.drop_column('source_snapshot_key')
        batch_op.drop_column('snapshot_kind')
        batch_op.drop_column('source')
        batch_op.drop_column('player_key')
        batch_op.drop_column('player_id')
        batch_op.drop_column('event_start_time')
        batch_op.drop_column('source_event_id')
