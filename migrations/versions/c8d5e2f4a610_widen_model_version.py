"""Preserve long model version labels when copying SQLite into PostgreSQL.

Revision ID: c8d5e2f4a610
Revises: b7e4a1c2d903
"""

import sqlalchemy as sa
from alembic import op


revision = 'c8d5e2f4a610'
down_revision = 'b7e4a1c2d903'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('model_metadata') as batch_op:
        batch_op.alter_column(
            'version',
            existing_type=sa.String(40),
            type_=sa.String(80),
            existing_nullable=False,
        )


def downgrade():
    connection = op.get_bind()
    overlong = connection.execute(
        sa.text('SELECT COUNT(*) FROM model_metadata WHERE LENGTH(version) > 40')
    ).scalar_one()
    if overlong:
        raise RuntimeError('Cannot narrow model_metadata.version with long labels.')
    with op.batch_alter_table('model_metadata') as batch_op:
        batch_op.alter_column(
            'version',
            existing_type=sa.String(80),
            type_=sa.String(40),
            existing_nullable=False,
        )
