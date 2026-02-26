"""Merge migration heads

Revision ID: c6c23cce68d8
Revises: a3f2c8e91d05, c1a2b3d4e5f6
Create Date: 2026-02-26 17:52:47.125625

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'c6c23cce68d8'
down_revision = ('a3f2c8e91d05', 'c1a2b3d4e5f6')
branch_labels = None
depends_on = None


def upgrade():
    pass


def downgrade():
    pass
