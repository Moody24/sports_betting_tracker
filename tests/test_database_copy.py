"""SQLite-to-PostgreSQL copy engine tests using disposable SQL databases."""

import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

import click
from sqlalchemy import create_engine, func, select

from app import db
from app.cli.database_commands import _validate_url_pair, migrate_database
from app.models import Bet, User


class TestDatabaseCopy(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        root = Path(self.tempdir.name)
        self.source = create_engine(f'sqlite:///{root / "source.sqlite"}')
        self.target = create_engine(f'sqlite:///{root / "target.sqlite"}')
        db.metadata.create_all(self.source)
        db.metadata.create_all(self.target)
        with self.source.begin() as connection:
            connection.execute(
                User.__table__.insert(),
                {
                    'id': 7,
                    'username': 'copy-user',
                    'email': 'copy@example.com',
                    'password_hash': 'not-a-real-password-hash',
                },
            )
            connection.execute(
                Bet.__table__.insert(),
                {
                    'id': 11,
                    'user_id': 7,
                    'team_a': 'Lakers',
                    'team_b': 'Celtics',
                    'match_date': datetime(2026, 1, 1, 18, tzinfo=timezone.utc),
                    'bet_amount': 25.0,
                    'outcome': 'pending',
                    'bet_type': 'moneyline',
                },
            )

    def test_copy_preserves_ids_counts_and_domain_totals(self):
        report = migrate_database(
            self.source,
            self.target,
            batch_size=1,
            dry_run=False,
            validate_only=False,
        )
        self.assertTrue(report['valid'])
        self.assertTrue(report['domain_metrics_match'])
        with self.target.connect() as connection:
            self.assertEqual(
                connection.execute(select(User.id)).scalar_one(),
                7,
            )
            self.assertEqual(
                connection.execute(select(func.count()).select_from(Bet)).scalar_one(),
                1,
            )

    def test_dry_run_leaves_target_empty(self):
        report = migrate_database(
            self.source,
            self.target,
            batch_size=50,
            dry_run=True,
            validate_only=False,
        )
        self.assertTrue(report['dry_run'])
        with self.target.connect() as connection:
            self.assertEqual(
                connection.execute(select(func.count()).select_from(User)).scalar_one(),
                0,
            )

    def test_copy_refuses_nonempty_target(self):
        with self.target.begin() as connection:
            connection.execute(
                User.__table__.insert(),
                {
                    'id': 2,
                    'username': 'existing',
                    'email': 'existing@example.com',
                    'password_hash': 'existing-hash',
                },
            )
        with self.assertRaisesRegex(click.ClickException, 'must be empty'):
            migrate_database(
                self.source,
                self.target,
                batch_size=50,
                dry_run=False,
                validate_only=False,
            )

    def test_validate_only_reports_mismatch(self):
        report = migrate_database(
            self.source,
            self.target,
            batch_size=50,
            dry_run=False,
            validate_only=True,
        )
        self.assertFalse(report['valid'])
        self.assertFalse(report['tables']['user']['match'])

    def test_cli_contract_rejects_wrong_engines_and_same_database(self):
        with self.assertRaisesRegex(click.ClickException, 'Source must'):
            _validate_url_pair(
                'postgresql://source/db',
                'postgresql://target/db',
            )
        with self.assertRaisesRegex(click.ClickException, 'Target must'):
            _validate_url_pair('/tmp/source.sqlite', 'sqlite:////tmp/target.sqlite')
        with self.assertRaisesRegex(click.ClickException, 'must be different'):
            _validate_url_pair(
                'sqlite:////tmp/source.sqlite',
                'sqlite:////tmp/source.sqlite',
                require_postgres=False,
            )


if __name__ == '__main__':
    unittest.main()
