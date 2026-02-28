"""add unit size and bet units

Revision ID: 12d4e93f7a10
Revises: 9f4a7c2d1eab
Create Date: 2026-02-28 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '12d4e93f7a10'
down_revision = '9f4a7c2d1eab'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('user', sa.Column('unit_size', sa.Float(), nullable=True))
    op.add_column('bet', sa.Column('units', sa.Float(), nullable=True))


def downgrade():
    op.drop_column('bet', 'units')
    op.drop_column('user', 'unit_size')

