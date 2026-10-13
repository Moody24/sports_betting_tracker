"""Exercise the bulk-copy codec against an actual PostgreSQL connection."""

import os
import unittest
from datetime import datetime

from sqlalchemy import Boolean, Column, DateTime, Integer, JSON, MetaData, String, Table
from sqlalchemy import create_engine, select

from app.cli.database_commands import _insert_batch


@unittest.skipUnless(
    os.getenv('DATABASE_URL', '').startswith('postgresql:'),
    'PostgreSQL DATABASE_URL required',
)
class PostgreSQLCopyTests(unittest.TestCase):
    def test_bulk_copy_preserves_escaped_values_and_nulls(self):
        table = Table(
            'copy_codec_test', MetaData(),
            Column('id', Integer, primary_key=True),
            Column('label', String(80)),
            Column('payload', JSON),
            Column('occurred_at', DateTime),
            Column('enabled', Boolean),
            prefixes=['TEMPORARY'],
        )
        engine = create_engine(os.environ['DATABASE_URL'])
        try:
            with engine.begin() as connection:
                table.create(connection)
                _insert_batch(connection, table, [
                    {'id': 1, 'label': 'tab\tline\nslash\\N',
                     'payload': {'text': 'quote " and \\ slash'},
                     'occurred_at': datetime(2026, 10, 1, 12, 0),
                     'enabled': True},
                    {'id': 2, 'label': None, 'payload': None,
                     'occurred_at': None, 'enabled': False},
                ])
                rows = connection.execute(
                    select(table).order_by(table.c.id)
                ).mappings().all()
                self.assertEqual(rows[0]['label'], 'tab\tline\nslash\\N')
                self.assertEqual(rows[0]['payload'],
                                 {'text': 'quote " and \\ slash'})
                self.assertEqual(rows[0]['occurred_at'],
                                 datetime(2026, 10, 1, 12, 0))
                self.assertTrue(rows[0]['enabled'])
                self.assertIsNone(rows[1]['label'])
                self.assertIsNone(rows[1]['payload'])
                self.assertFalse(rows[1]['enabled'])
        finally:
            engine.dispose()
