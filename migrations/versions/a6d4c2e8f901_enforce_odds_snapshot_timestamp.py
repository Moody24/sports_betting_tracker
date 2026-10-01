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
        inspector = sa.inspect(connection)
        constraints = inspector.get_unique_constraints('bet_postmortem')
        if not any(c['name'] == 'bet_postmortem_bet_id_key' for c in constraints):
            indexes = inspector.get_indexes('bet_postmortem')
            unique_index = next(
                (index for index in indexes
                 if index['unique'] and index['column_names'] == ['bet_id']),
                None,
            )
            with op.batch_alter_table('bet_postmortem') as batch_op:
                if unique_index:
                    batch_op.drop_index(unique_index['name'])
                else:
                    old_constraint = next(
                        (c for c in constraints if c['column_names'] == ['bet_id']),
                        None,
                    )
                    if old_constraint:
                        batch_op.drop_constraint(old_constraint['name'], type_='unique')
                batch_op.create_unique_constraint(
                    'bet_postmortem_bet_id_key', ['bet_id']
                )


def downgrade():
    with op.batch_alter_table('odds_snapshots') as batch_op:
        batch_op.alter_column(
            'snapped_at',
            existing_type=sa.DateTime(timezone=True),
            nullable=True,
        )
