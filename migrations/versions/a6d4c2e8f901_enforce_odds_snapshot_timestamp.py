"""enforce odds snapshot timestamp

Revision ID: a6d4c2e8f901
Revises: e9602669917f
Create Date: 2026-09-03 12:20:00.000000
"""

import sqlalchemy as sa
from alembic import op


revision = 'a6d4c2e8f901'
down_revision = 'e9602669917f'
branch_labels = None
depends_on = None


def upgrade():
    connection = op.get_bind()
    missing = connection.execute(
        sa.text('SELECT COUNT(*) FROM odds_snapshots WHERE snapped_at IS NULL')
    ).scalar_one()
    if missing:
        raise RuntimeError(
            'Cannot require odds_snapshots.snapped_at while null rows exist; '
            'repair or remove those rows before retrying the migration.'
        )

    with op.batch_alter_table('odds_snapshots') as batch_op:
        batch_op.alter_column(
            'snapped_at',
            existing_type=sa.DateTime(timezone=True),
            nullable=False,
        )

    if connection.dialect.name == 'sqlite':
        naming_convention = {
            'uq': 'uq_%(table_name)s_%(column_0_name)s',
        }
        with op.batch_alter_table(
            'bet_postmortem',
            naming_convention=naming_convention,
        ) as batch_op:
            batch_op.drop_constraint(
                'uq_bet_postmortem_bet_id',
                type_='unique',
            )
            batch_op.create_unique_constraint(
                'bet_postmortem_bet_id_key',
                ['bet_id'],
            )


def downgrade():
    with op.batch_alter_table('odds_snapshots') as batch_op:
        batch_op.alter_column(
            'snapped_at',
            existing_type=sa.DateTime(timezone=True),
            nullable=True,
        )
