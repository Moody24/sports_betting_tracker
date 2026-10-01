"""Upgrade the SQLite shape that existed before the timestamp migration."""

import importlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, inspect


class LegacySQLiteMigrationTests(unittest.TestCase):
    def test_unique_index_is_replaced_without_losing_existing_rows(self):
        migration = importlib.import_module(
            'migrations.versions.a6d4c2e8f901_enforce_odds_snapshot_timestamp'
        )
        with tempfile.TemporaryDirectory() as directory:
            engine = create_engine(f'sqlite:///{Path(directory) / "legacy.sqlite"}')
            with engine.begin() as connection:
                connection.exec_driver_sql(
                    'CREATE TABLE odds_snapshots ('
                    'id INTEGER PRIMARY KEY, snapped_at DATETIME)'
                )
                connection.exec_driver_sql(
                    'CREATE TABLE bet_postmortem ('
                    'id INTEGER PRIMARY KEY, bet_id INTEGER NOT NULL)'
                )
                connection.exec_driver_sql(
                    'CREATE UNIQUE INDEX ix_bet_postmortem_bet_id '
                    'ON bet_postmortem (bet_id)'
                )
                connection.exec_driver_sql(
                    'INSERT INTO bet_postmortem (id, bet_id) VALUES (1, 7)'
                )
                operations = Operations(MigrationContext.configure(connection))
                with patch.object(migration, 'op', operations):
                    migration.upgrade()

                self.assertEqual(
                    connection.exec_driver_sql(
                        'SELECT bet_id FROM bet_postmortem'
                    ).scalar_one(),
                    7,
                )
                constraints = inspect(connection).get_unique_constraints(
                    'bet_postmortem'
                )
                self.assertIn(
                    'bet_postmortem_bet_id_key',
                    {constraint['name'] for constraint in constraints},
                )
                columns = inspect(connection).get_columns('odds_snapshots')
                self.assertFalse(next(
                    column['nullable'] for column in columns
                    if column['name'] == 'snapped_at'
                ))
