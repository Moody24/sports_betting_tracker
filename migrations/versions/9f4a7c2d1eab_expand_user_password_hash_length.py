"""Expand user.password_hash length to support werkzeug scrypt hashes

Revision ID: 9f4a7c2d1eab
Revises: 57622ba64816
Create Date: 2026-02-26 18:10:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '9f4a7c2d1eab'
down_revision = '57622ba64816'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('user', schema=None) as batch_op:
        batch_op.alter_column(
            'password_hash',
            existing_type=sa.String(length=128),
            type_=sa.String(length=512),
            existing_nullable=False,
        )


def downgrade():
    with op.batch_alter_table('user', schema=None) as batch_op:
        batch_op.alter_column(
            'password_hash',
            existing_type=sa.String(length=512),
            type_=sa.String(length=128),
            existing_nullable=False,
        )
