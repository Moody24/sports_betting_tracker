"""Track verified player identities and source freshness.

Revision ID: d93f1a7c2b40
Revises: c8d5e2f4a610
"""

import sqlalchemy as sa
from alembic import op


revision = 'd93f1a7c2b40'
down_revision = 'c8d5e2f4a610'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'player_identity',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('espn_id', sa.String(20), nullable=False, unique=True),
        sa.Column('nba_id', sa.String(20), unique=True),
        sa.Column('player_name', sa.String(120), nullable=False),
        sa.Column('name_key', sa.String(120), nullable=False),
        sa.Column('team_abbr', sa.String(10)),
        sa.Column('status', sa.String(20), nullable=False),
        sa.Column('evidence', sa.String(160)),
        sa.Column('checked_at', sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index('ix_player_identity_name_key', 'player_identity', ['name_key'])
    op.create_table(
        'data_fetch_receipt',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('feed', sa.String(60), nullable=False),
        sa.Column('status', sa.String(20), nullable=False),
        sa.Column('fetched_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('data_through', sa.Date()),
        sa.Column('source_version', sa.String(120)),
        sa.Column('row_count', sa.Integer(), nullable=False),
        sa.Column('detail', sa.String(300)),
    )
    op.create_index('ix_data_fetch_receipt_feed', 'data_fetch_receipt', ['feed'])
    with op.batch_alter_table('team_defense_snapshot') as batch:
        batch.add_column(sa.Column('source', sa.String(40), nullable=False,
                                   server_default='legacy_unverified'))
        batch.add_column(sa.Column('observed_through', sa.Date()))
        batch.add_column(sa.Column('sample_games', sa.Integer()))
    with op.batch_alter_table('injury_report') as batch:
        batch.add_column(sa.Column('source', sa.String(40), nullable=False,
                                   server_default='legacy_unverified'))


def downgrade():
    with op.batch_alter_table('injury_report') as batch:
        batch.drop_column('source')
    with op.batch_alter_table('team_defense_snapshot') as batch:
        batch.drop_column('sample_games')
        batch.drop_column('observed_through')
        batch.drop_column('source')
    op.drop_index('ix_data_fetch_receipt_feed', table_name='data_fetch_receipt')
    op.drop_table('data_fetch_receipt')
    op.drop_index('ix_player_identity_name_key', table_name='player_identity')
    op.drop_table('player_identity')
