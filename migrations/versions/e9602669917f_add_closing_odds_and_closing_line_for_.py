"""add closing_odds and closing_line for CLV

Both nullable and left unpopulated: nothing captures closing prices yet, so
every existing row is legitimately unknown. Backfilling a default would make
"we never captured the close" indistinguishable from "the line did not move".

Revision ID: e9602669917f
Revises: 7ca81e2b4f10
Create Date: 2026-08-11 03:09:50.256276

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'e9602669917f'
down_revision = '7ca81e2b4f10'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('bet', schema=None) as batch_op:
        batch_op.add_column(sa.Column('closing_odds', sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column('closing_line', sa.Float(), nullable=True))


def downgrade():
    with op.batch_alter_table('bet', schema=None) as batch_op:
        batch_op.drop_column('closing_line')
        batch_op.drop_column('closing_odds')
