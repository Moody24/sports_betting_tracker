"""Add GameSnapshot table and bonus_multiplier to Bet

Revision ID: a3f2c8e91d05
Revises: 1594c8d21bfb
Create Date: 2026-02-25 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'a3f2c8e91d05'
down_revision = '1594c8d21bfb'
branch_labels = None
depends_on = None


def upgrade():
    # Add bonus_multiplier to bet table
    with op.batch_alter_table('bet', schema=None) as batch_op:
        batch_op.add_column(
            sa.Column('bonus_multiplier', sa.Float(), nullable=False, server_default='1.0')
        )

    # Create game_snapshot table
    op.create_table(
        'game_snapshot',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('espn_id', sa.String(length=80), nullable=False),
        sa.Column('game_date', sa.Date(), nullable=False),
        sa.Column('home_team', sa.String(length=100), nullable=False),
        sa.Column('away_team', sa.String(length=100), nullable=False),
        sa.Column('home_logo', sa.String(length=300), nullable=True),
        sa.Column('away_logo', sa.String(length=300), nullable=True),
        sa.Column('home_score', sa.Integer(), nullable=True),
        sa.Column('away_score', sa.Integer(), nullable=True),
        sa.Column('status', sa.String(length=40), nullable=False, server_default='STATUS_SCHEDULED'),
        sa.Column('over_under_line', sa.Float(), nullable=True),
        sa.Column('moneyline_home', sa.Integer(), nullable=True),
        sa.Column('moneyline_away', sa.Integer(), nullable=True),
        sa.Column('props_json', sa.Text(), nullable=True),
        sa.Column('snapshot_time', sa.DateTime(), nullable=False),
        sa.Column('is_final', sa.Boolean(), nullable=False, server_default='0'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_game_snapshot_espn_id', 'game_snapshot', ['espn_id'], unique=False)


def downgrade():
    op.drop_index('ix_game_snapshot_espn_id', table_name='game_snapshot')
    op.drop_table('game_snapshot')

    with op.batch_alter_table('bet', schema=None) as batch_op:
        batch_op.drop_column('bonus_multiplier')
