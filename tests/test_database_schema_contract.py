"""ORM declarations that must remain aligned with the migration chain."""

import unittest

from sqlalchemy import UniqueConstraint

from app.models import (
    BetPostmortem,
    GameSnapshot,
    JobLog,
    ModelMetadata,
    OddsSnapshot,
    PlayerGameLog,
    TeamDefenseSnapshot,
)


class DatabaseSchemaContractTests(unittest.TestCase):
    def test_migration_owned_indexes_are_declared_in_metadata(self):
        expected = {
            GameSnapshot: {'ix_game_snapshot_espn_id_game_date'},
            PlayerGameLog: {
                'ix_player_game_log_player_name',
                'ix_player_game_log_player_date',
                'ix_player_game_log_cache_expires',
            },
            TeamDefenseSnapshot: {'ix_team_defense_snapshot_team_name'},
            ModelMetadata: {'ix_model_metadata_model_name_is_active'},
            JobLog: {'ix_job_log_started_at'},
            BetPostmortem: {
                'ix_bet_postmortem_bet_id',
                'ix_bet_postmortem_primary_reason',
                'ix_bet_postmortem_created_at_stat_type',
            },
        }

        for model, index_names in expected.items():
            with self.subTest(table=model.__tablename__):
                declared = {index.name for index in model.__table__.indexes}
                self.assertTrue(index_names <= declared)

    def test_postmortem_unique_constraint_matches_postgresql_name(self):
        constraints = {
            constraint.name: tuple(column.name for column in constraint.columns)
            for constraint in BetPostmortem.__table__.constraints
            if isinstance(constraint, UniqueConstraint)
        }
        self.assertEqual(
            ('bet_id',),
            constraints['bet_postmortem_bet_id_key'],
        )

    def test_odds_snapshot_timestamp_is_required(self):
        self.assertFalse(OddsSnapshot.__table__.c.snapped_at.nullable)


if __name__ == '__main__':
    unittest.main()
