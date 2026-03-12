"""add_parlay_leg_count_indexes_and_constraints

Add parlay_leg_count column to bet, composite indexes for common query
patterns, CHECK constraints for data integrity (PostgreSQL only), and
drop redundant single-column indexes on odds_snapshot.

Revision ID: f1a2b3c4d5e6
Revises: e7a3f1c9b204
Create Date: 2026-03-11 00:00:00.000000
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.engine.reflection import Inspector


# revision identifiers, used by Alembic.
revision = 'f1a2b3c4d5e6'
down_revision = 'e7a3f1c9b204'
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    dialect = bind.dialect.name

    # ── New column ────────────────────────────────────────────────────
    op.add_column('bet', sa.Column('parlay_leg_count', sa.Integer(), nullable=True))

    # ── Composite indexes ─────────────────────────────────────────────
    op.create_index('ix_bet_user_outcome_date', 'bet', ['user_id', 'outcome', 'match_date'])
    op.create_index('ix_player_game_log_date_name', 'player_game_log', ['game_date', 'player_name'])

    # ── CHECK constraints (PostgreSQL only) ───────────────────────────
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

    # ── Drop redundant single-column OddsSnapshot indexes ────────────
    inspector = Inspector.from_engine(bind)
    existing_indexes = {idx['name'] for idx in inspector.get_indexes('odds_snapshot')}
    if 'ix_odds_snapshot_game_date' in existing_indexes:
        op.drop_index('ix_odds_snapshot_game_date', table_name='odds_snapshot')
    if 'ix_odds_snapshot_game_id' in existing_indexes:
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

    op.drop_index('ix_bet_user_outcome_date', table_name='bet')
    op.drop_index('ix_player_game_log_date_name', table_name='player_game_log')
    op.drop_column('bet', 'parlay_leg_count')
