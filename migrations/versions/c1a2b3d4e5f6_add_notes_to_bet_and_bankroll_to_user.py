"""add notes to bet and starting_bankroll to user

Revision ID: c1a2b3d4e5f6
Revises: 8b9478dd7d82
Create Date: 2026-02-25 10:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'c1a2b3d4e5f6'
down_revision = '8b9478dd7d82'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('bet', schema=None) as batch_op:
        batch_op.add_column(sa.Column('notes', sa.Text(), nullable=True))

    with op.batch_alter_table('user', schema=None) as batch_op:
        batch_op.add_column(sa.Column('starting_bankroll', sa.Float(), nullable=True))


def downgrade():
    with op.batch_alter_table('user', schema=None) as batch_op:
        batch_op.drop_column('starting_bankroll')

    with op.batch_alter_table('bet', schema=None) as batch_op:
        batch_op.drop_column('notes')
