"""Durable shared games collection with fixture HTTP only."""

import importlib
import json
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import requests
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, inspect

from app import db
from tests.helpers import BaseTestCase


NOW = datetime(2026, 10, 21, 1, 0, tzinfo=timezone.utc)


def game(score=10, **changes):
    row = {
        'id': 77, 'date': '2026-10-20', 'datetime': '2026-10-21T00:00:00Z',
        'home_team': {'full_name': 'Boston Celtics', 'abbreviation': 'BOS'},
        'visitor_team': {'full_name': 'Los Angeles Lakers', 'abbreviation': 'LAL'},
        'home_team_score': score, 'visitor_team_score': 9,
        'status_state': 'in_progress', 'status': '1st Qtr', 'period': 1, 'time': '3:44',
    }
    row.update(changes)
    return row


def response(rows=None, *, status=200, cursor=None, headers=None):
    result = requests.Response()
    result.status_code = status
    result._content = json.dumps({
        'data': rows if rows is not None else [game()],
        'meta': {'next_cursor': cursor, 'per_page': 100},
    }).encode()
    result._content_consumed = True
    result.headers.update(headers or {})
    return result


class BasketballFeedTests(BaseTestCase):
    def setUp(self):
        super().setUp()
        self.app.config['BALLDONTLIE_API_KEY'] = 'fixture-secret'
        context = self.app.app_context()
        context.push()
        self.addCleanup(context.pop)
        self.http = patch('app.services.basketball_feed.requests.get')
        self.get = self.http.start()
        self.addCleanup(self.http.stop)
        self.get.return_value = response()

    def collect(self, at=NOW):
        from app.services.basketball_feed import collect_basketball_feed
        return collect_basketball_feed(now=at)

    def test_disabled_never_calls_http_or_exposes_key(self):
        from app.services.basketball_feed import get_feed_snapshot
        self.app.config['BALLDONTLIE_API_KEY'] = ''
        result = self.collect()
        self.assertEqual(result['state'], 'disabled')
        self.assertEqual(result['games'], [])
        self.get.assert_not_called()
        self.assertNotIn('fixture-secret', json.dumps(get_feed_snapshot(now=NOW)))

    def test_success_normalizes_et_dates_namespaces_and_nulls(self):
        self.get.return_value = response([game(visitor_team_score=None)])
        result = self.collect()
        row = result['games'][0]
        self.assertEqual(result['state'], 'current')
        self.assertEqual(row['match_date'], '2026-10-20')
        self.assertEqual(row['source_event_id'], 'balldontlie:77')
        self.assertEqual(row['home']['score'], 10)
        self.assertIsNone(row['away']['score'])
        self.assertEqual(row['status'], 'in_progress')
        self.assertEqual(row['clock'], '3:44')
        self.assertEqual(result['last_success_at'], NOW.isoformat())
        self.assertNotIn('source_updated_at', result)
        kwargs = self.get.call_args.kwargs
        self.assertEqual(kwargs['params'], {'dates[]': '2026-10-20', 'per_page': 100})
        self.assertEqual(kwargs['timeout'], 5)
        self.assertEqual(kwargs['headers']['Authorization'], 'fixture-secret')
        self.assertFalse(kwargs['allow_redirects'])

    def test_duplicate_updates_fetch_time_without_revision(self):
        from app.models import BasketballFeedEvent, BasketballFeedState
        first = self.collect()
        second = self.collect(NOW + timedelta(seconds=30))
        self.assertEqual(first['revision'], second['revision'])
        self.assertNotEqual(first['last_success_at'], second['last_success_at'])
        self.assertEqual(BasketballFeedEvent.query.count(), 1)
        db.session.remove()
        self.assertEqual(db.session.get(BasketballFeedState, 'nba_games').revision,
                         first['revision'])

    def test_changed_scores_create_durable_ascending_revisions_and_replay(self):
        from app.services.basketball_feed import read_feed_events
        first = self.collect()
        self.get.return_value = response([game(score=12)])
        second = self.collect(NOW + timedelta(seconds=30))
        self.assertGreater(second['revision'], first['revision'])
        db.session.remove()
        replay = read_feed_events(str(first['revision']), now=NOW + timedelta(seconds=30))
        self.assertFalse(replay['reset'])
        self.assertEqual([e['revision'] for e in replay['events']], [second['revision']])
        self.assertEqual(replay['events'][0]['payload']['games'][0]['home']['score'], 12)

    def test_repeated_ticks_cannot_exceed_poll_budget(self):
        self.collect()
        for elapsed in (0, 1, 12, 29):
            self.collect(NOW + timedelta(seconds=elapsed))
        self.assertEqual(self.get.call_count, 1)
        self.collect(NOW + timedelta(seconds=30))
        self.assertEqual(self.get.call_count, 2)

    def test_timeout_retains_bounded_stale_and_backoff_survives_session(self):
        from app.services.basketball_feed import get_feed_snapshot
        from app.models import BasketballFeedState
        first = self.collect()
        self.get.side_effect = requests.Timeout('secret and URL must never escape')
        failed = self.collect(NOW + timedelta(seconds=30))
        self.assertEqual(failed['state'], 'stale')
        self.assertEqual(failed['games'], first['games'])
        self.assertEqual(failed['last_success_at'], first['last_success_at'])
        self.assertNotIn('secret', json.dumps(failed))
        db.session.remove()
        self.collect(NOW + timedelta(seconds=59))
        self.assertEqual(self.get.call_count, 2)
        self.collect(NOW + timedelta(seconds=60))
        row = db.session.get(BasketballFeedState, 'nba_games')
        self.assertEqual(row.retry_at.replace(tzinfo=timezone.utc), NOW + timedelta(seconds=120))
        old = get_feed_snapshot(now=NOW + timedelta(minutes=16))
        self.assertEqual(old['state'], 'unavailable')
        self.assertEqual(old['games'], [])

    def test_retry_after_is_honored_without_inline_retries(self):
        self.get.return_value = response(status=429, headers={'Retry-After': '180'})
        failed = self.collect()
        self.assertEqual(failed['error_code'], 'http_429')
        self.collect(NOW + timedelta(seconds=179))
        self.assertEqual(self.get.call_count, 1)
        self.collect(NOW + timedelta(seconds=180))
        self.assertEqual(self.get.call_count, 2)

    def test_partial_pagination_is_never_published_as_complete(self):
        self.get.return_value = response(cursor=100)
        result = self.collect()
        self.assertEqual(result['state'], 'unavailable')
        self.assertEqual(result['games'], [])
        self.assertEqual(result['error_code'], 'incomplete_page')
        self.get.assert_called_once()

    def test_midnight_rollover_clears_yesterday_even_during_backoff(self):
        from app.services.basketball_feed import get_feed_snapshot
        before = datetime(2026, 10, 21, 3, 59, 50, tzinfo=timezone.utc)
        self.collect(before)
        after = before + timedelta(seconds=15)
        snapshot = get_feed_snapshot(now=after)
        self.assertEqual(snapshot['games'], [])
        self.assertEqual(snapshot['match_date'], '2026-10-21')
        self.assertEqual(snapshot['state'], 'unavailable')
        rolled = self.collect(after)
        self.assertEqual(rolled['games'], [])
        self.assertEqual(get_feed_snapshot(now=after)['state'], 'unavailable')
        self.assertEqual(self.get.call_count, 1)

    def test_disabling_and_reenabling_does_not_claim_a_cleared_slate_is_current(self):
        from app.services.basketball_feed import get_feed_snapshot
        self.collect()
        self.app.config['BALLDONTLIE_API_KEY'] = ''
        self.collect(NOW + timedelta(seconds=5))
        self.app.config['BALLDONTLIE_API_KEY'] = 'fixture-secret'
        self.assertEqual(get_feed_snapshot(now=NOW + timedelta(seconds=10))['state'], 'unavailable')

    def test_fetch_crossing_et_midnight_cannot_validate_the_next_days_slate(self):
        from app.services.basketball_feed import (
            collect_basketball_feed, get_feed_snapshot, read_feed_events,
        )
        before = datetime(2026, 10, 21, 3, 59, 59, tzinfo=timezone.utc)
        after = before + timedelta(seconds=2)
        with patch('app.services.basketball_feed._now', side_effect=[before, after]):
            published = collect_basketball_feed()
        self.assertEqual(self.get.call_args.kwargs['params']['dates[]'], '2026-10-20')
        self.assertEqual(published['state'], 'unavailable')
        self.assertEqual(published['last_success_at'], after.isoformat())
        db.session.remove()
        for result in (get_feed_snapshot(now=after),
                       read_feed_events(0, now=after)['snapshot'],
                       self.collect(after + timedelta(seconds=10))):
            self.assertEqual(result['match_date'], '2026-10-21')
            self.assertEqual(result['state'], 'unavailable')
            self.assertEqual(result['games'], [])
        self.get.assert_called_once()
        self.get.return_value = response([game(date='2026-10-21',
                                              datetime='2026-10-21T23:00:00Z')])
        fresh = self.collect(before + timedelta(seconds=30))
        self.assertEqual(fresh['state'], 'current')
        self.assertEqual(fresh['games'][0]['match_date'], '2026-10-21')
        self.assertEqual(self.get.call_count, 2)

    def test_auth_error_is_safe_and_waits_five_minutes(self):
        self.get.return_value = response(status=401)
        result = self.collect()
        self.assertEqual(result['state'], 'unavailable')
        self.assertEqual(result['error_code'], 'http_401')
        self.collect(NOW + timedelta(seconds=299))
        self.get.assert_called_once()

    def test_response_size_and_malformed_json_are_rejected(self):
        oversized = response()
        oversized._content = b' ' * (1024 * 1024 + 1)
        self.get.return_value = oversized
        self.assertEqual(self.collect()['error_code'], 'response_too_large')
        malformed = response()
        malformed._content = b'not JSON containing fixture-secret'
        self.get.return_value = malformed
        result = self.collect(NOW + timedelta(seconds=30))
        self.assertEqual(result['error_code'], 'invalid_payload')
        self.assertNotIn('fixture-secret', json.dumps(result))

    def test_backoff_caps_at_five_minutes(self):
        from app.models import BasketballFeedState
        self.get.side_effect = requests.Timeout()
        at = NOW
        for delay in (30, 60, 120, 240, 300, 300):
            self.collect(at)
            row = db.session.get(BasketballFeedState, 'nba_games')
            next_attempt = row.retry_at.replace(tzinfo=timezone.utc)
            self.assertEqual((next_attempt - at).total_seconds(), delay)
            at = next_attempt

    def test_invalid_games_and_duplicate_ids_reject_whole_response(self):
        for rows in ([game(), game()], [game(id='bad')], [game(home_team={})]):
            with self.subTest(rows=rows):
                self.get.return_value = response(rows)
                result = self.collect()
                self.assertEqual(result['state'], 'unavailable')
                self.assertEqual(result['error_code'], 'invalid_payload')
                from app.models import BasketballFeedState
                row = db.session.get(BasketballFeedState, 'nba_games')
                row.retry_at = None
                row.last_attempt_at = None
                db.session.commit()

    def test_empty_slate_is_valid_but_read_helpers_never_fetch(self):
        from app.services.basketball_feed import get_feed_snapshot, read_feed_events
        self.get.return_value = response([])
        result = self.collect()
        self.assertEqual(result['state'], 'current')
        self.assertEqual(result['games'], [])
        for _ in range(4):
            get_feed_snapshot(now=NOW)
            read_feed_events(result['revision'], now=NOW)
        self.get.assert_called_once()

    def test_expired_replay_resets_to_truthful_latest_snapshot(self):
        from app.services.basketball_feed import read_feed_events
        self.collect()
        replay = read_feed_events(0, now=NOW + timedelta(minutes=2))
        self.assertTrue(replay['reset'])
        self.assertEqual(replay['snapshot']['state'], 'stale')
        self.assertEqual(replay['events'], [])

    def test_retry_after_http_date_and_missing_source_start_time(self):
        self.get.return_value = response(status=503, headers={
            'Retry-After': 'Wed, 21 Oct 2026 01:02:00 GMT',
        })
        self.collect()
        self.collect(NOW + timedelta(seconds=119))
        self.get.assert_called_once()
        self.get.return_value = response([game(datetime=None, status_state='scheduled',
                                              period=0, time=' ', home_team_score=0)])
        result = self.collect(NOW + timedelta(seconds=120))
        self.assertIsNone(result['games'][0]['start_time'])
        self.assertEqual(result['games'][0]['home']['score'], 0)
        self.assertEqual(result['games'][0]['match_date'], '2026-10-20')

    def test_event_journal_is_bounded_and_old_ahead_invalid_ids_reset(self):
        from app.models import BasketballFeedEvent
        from app.services.basketball_feed import read_feed_events
        with patch('app.services.basketball_feed.MAX_EVENTS', 3):
            first = self.collect()
            for index in range(1, 5):
                self.get.return_value = response([game(score=10 + index)])
                latest = self.collect(NOW + timedelta(seconds=30 * index))
        self.assertEqual(BasketballFeedEvent.query.count(), 3)
        for revision in (None, 'bad', '-1', first['revision'], latest['revision'] + 5):
            replay = read_feed_events(revision, now=NOW + timedelta(seconds=120))
            self.assertTrue(replay['reset'])
            self.assertEqual(replay['events'], [])
            self.assertEqual(replay['snapshot']['revision'], latest['revision'])


class BasketballFeedMigrationTests(unittest.TestCase):
    def test_upgrade_matches_models_and_downgrade_and_ids_do_not_recycle(self):
        from app.models import BasketballFeedEvent, BasketballFeedState
        migration = importlib.import_module(
            'migrations.versions.ea7026bd109c_add_shared_basketball_feed')
        engine = create_engine('sqlite://')
        self.addCleanup(engine.dispose)
        with engine.begin() as connection:
            operations = Operations(MigrationContext.configure(connection))
            with patch.object(migration, 'op', operations):
                migration.upgrade()
                for model in (BasketballFeedState, BasketballFeedEvent):
                    actual = {column['name'] for column in inspect(connection).get_columns(
                        model.__tablename__)}
                    self.assertEqual(actual, set(model.__table__.columns.keys()))
                for _ in range(2):
                    connection.exec_driver_sql(
                        "INSERT INTO basketball_feed_event (key, payload_json, created_at) "
                        "VALUES ('nba_games', '{}', '2026-10-20 00:00:00')")
                    event_id = connection.exec_driver_sql(
                        'SELECT id FROM basketball_feed_event').scalar_one()
                    connection.exec_driver_sql('DELETE FROM basketball_feed_event')
                self.assertEqual(event_id, 2)
                migration.downgrade()
                self.assertNotIn('basketball_feed_state', inspect(connection).get_table_names())
                self.assertNotIn('basketball_feed_event', inspect(connection).get_table_names())
