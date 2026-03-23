"""add_model_metadata_composite_index

Adds composite index on model_metadata(model_name, is_active) to eliminate
75k+ sequential scans caused by the active-model lookup on every ML prediction.
Also adds index on team_defense_snapshot(team_name) to reduce 52k seq scans
from the matchup-adjustment query.

Revision ID: deef0459a5af
Revises: 74721be2e47b
Create Date: 2026-03-22 20:44:47.313783

"""
from alembic import op


# revision identifiers, used by Alembic.
revision = 'deef0459a5af'
down_revision = '74721be2e47b'
branch_labels = None
depends_on = None


def upgrade():
    # Composite covering index: the active-model lookup filters on both
    # columns; model_name first for prefix scans, is_active second for
    # index-only filtering. PostgreSQL will use this instead of a seq scan.
    op.create_index(
        'ix_model_metadata_model_name_is_active',
        'model_metadata',
        ['model_name', 'is_active'],
    )
    # team_name index for matchup-service ilike queries.
    # ilike('%term%') with a leading wildcard can't use B-tree, but
    # ilike('term%') can. The matchup_service already normalises names
    # so a simple B-tree index handles the common exact/prefix patterns.
    op.create_index(
        'ix_team_defense_snapshot_team_name',
        'team_defense_snapshot',
        ['team_name'],
    )


def downgrade():
    op.drop_index('ix_team_defense_snapshot_team_name', table_name='team_defense_snapshot')
    op.drop_index('ix_model_metadata_model_name_is_active', table_name='model_metadata')
