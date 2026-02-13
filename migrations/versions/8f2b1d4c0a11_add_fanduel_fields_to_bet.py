"""Add FanDuel import fields to bet

Revision ID: 8f2b1d4c0a11
Revises: df40be11245b
Create Date: 2026-02-13 20:30:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '8f2b1d4c0a11'
down_revision = 'df40be11245b'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('bet', schema=None) as batch_op:
        batch_op.add_column(sa.Column('american_odds', sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column('is_parlay', sa.Boolean(), nullable=False, server_default=sa.false()))
        batch_op.add_column(sa.Column('source', sa.String(length=40), nullable=False, server_default='manual'))

    with op.batch_alter_table('bet', schema=None) as batch_op:
        batch_op.alter_column('is_parlay', server_default=None)
        batch_op.alter_column('source', server_default=None)


def downgrade():
    with op.batch_alter_table('bet', schema=None) as batch_op:
        batch_op.drop_column('source')
        batch_op.drop_column('is_parlay')
        batch_op.drop_column('american_odds')
