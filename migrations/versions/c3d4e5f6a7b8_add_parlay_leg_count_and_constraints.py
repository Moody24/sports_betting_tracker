"""add_parlay_leg_count_and_constraints

Adds:
  - bet.parlay_leg_count (nullable Integer) — eliminates N+1 in display_label
  - CHECK constraints on bet: outcome, bet_type, american_odds, parlay/parlay_id, bonus_multiplier
  - Drop redundant single-col OddsSnapshot indexes superseded by composite

Revision ID: c3d4e5f6a7b8
Revises: f1a2b3c4d5e6
Create Date: 2026-03-11 00:00:00.000000
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.engine.reflection import Inspector

revision = 'c3d4e5f6a7b8'
down_revision = 'f1a2b3c4d5e6'
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    dialect = bind.dialect.name

    # Add parlay_leg_count column
    op.add_column('bet', sa.Column('parlay_leg_count', sa.Integer(), nullable=True))

    # CHECK constraints — PostgreSQL only (SQLite used in tests doesn't support ALTER TABLE ADD CONSTRAINT)
    if dialect == 'postgresql':
        op.execute(
            "ALTER TABLE bet ADD CONSTRAINT ck_bet_outcome "
            "CHECK (outcome IN ('win', 'lose', 'pending', 'push'))"
        )
        op.execute(
            "ALTER TABLE bet ADD CONSTRAINT ck_bet_type "
            "CHECK (bet_type IN ('moneyline', 'over', 'under', 'spread', 'prop'))"
        )
        op.execute(
            "ALTER TABLE bet ADD CONSTRAINT ck_bet_american_odds "
            "CHECK (american_odds IS NULL OR american_odds != 0)"
        )
        op.execute(
            "ALTER TABLE bet ADD CONSTRAINT ck_bet_parlay "
            "CHECK (is_parlay = FALSE OR parlay_id IS NOT NULL)"
        )
        op.execute(
            "ALTER TABLE bet ADD CONSTRAINT ck_bet_bonus_multiplier "
            "CHECK (bonus_multiplier > 0)"
        )

    # Drop redundant single-column OddsSnapshot indexes (superseded by composite)
    inspector = Inspector.from_engine(bind)
    if 'odds_snapshot' in inspector.get_table_names():
        existing = {idx['name'] for idx in inspector.get_indexes('odds_snapshot')}
        if 'ix_odds_snapshot_game_date' in existing:
            op.drop_index('ix_odds_snapshot_game_date', table_name='odds_snapshot')
        if 'ix_odds_snapshot_game_id' in existing:
            op.drop_index('ix_odds_snapshot_game_id', table_name='odds_snapshot')


def downgrade():
    bind = op.get_bind()
    dialect = bind.dialect.name

    if dialect == 'postgresql':
        op.execute("ALTER TABLE bet DROP CONSTRAINT IF EXISTS ck_bet_outcome")
        op.execute("ALTER TABLE bet DROP CONSTRAINT IF EXISTS ck_bet_type")
        op.execute("ALTER TABLE bet DROP CONSTRAINT IF EXISTS ck_bet_american_odds")
        op.execute("ALTER TABLE bet DROP CONSTRAINT IF EXISTS ck_bet_parlay")
        op.execute("ALTER TABLE bet DROP CONSTRAINT IF EXISTS ck_bet_bonus_multiplier")

    op.drop_column('bet', 'parlay_leg_count')
