"""Shared SSE delivery uses durable DB reads, bounded memory, and no upstream."""

import runpy
import threading
import unittest
from datetime import timedelta
from pathlib import Path
from unittest.mock import Mock, patch

from sqlalchemy import event

from app import db
from app.services.feed_stream import FeedStreamHub, StreamCapacityError
from tests.helpers import BaseTestCase


def snapshot(revision=1, state='current'):
    return {
        'revision': revision, 'state': state, 'games': [],
        'last_success_at': '2026-10-07T12:00:00-04:00',
        'last_attempt_at': '2026-10-07T12:00:00-04:00',
        'provider_delay': 'Provider updates may be delayed.',
        'poll_interval_seconds': 30, 'match_date': '2026-10-07',
        'error_code': None,
    }


def result(revision=1, *, reset=False, events=(), state='current'):
    return {
        'reset': reset, 'snapshot': snapshot(revision, state),
        'events': [{'revision': value, 'payload': snapshot(value)} for value in events],
    }


class FeedStreamHubTests(unittest.TestCase):
    def hub(self, reader, **kwargs):
        hub = FeedStreamHub(reader, start_thread=False, **kwargs)
        self.addCleanup(hub.close)
        return hub

    def test_two_viewers_share_one_read_and_identical_events(self):
        reader = Mock(return_value=result(3, events=(2, 3)))
        hub = self.hub(reader)
        first, second = hub.subscribe('1'), hub.subscribe('1')
        hub.poll_once()
        reader.assert_called_once_with(1)
        for revision in (2, 3):
            event = first.next_event(0)
            self.assertEqual(event, second.next_event(0))
            self.assertEqual((event.event, event.revision), ('snapshot', revision))
        self.assertIsNone(first.next_event(0))

    def test_missing_invalid_and_ahead_cursors_reset_to_latest(self):
        for cursor in (None, '', 'NaN', '-1', '1.5', '9' * 400, '99'):
            with self.subTest(cursor=cursor):
                hub = self.hub(Mock(return_value=result(3, reset=True)))
                subscriber = hub.subscribe(cursor)
                hub.poll_once()
                event = subscriber.next_event(0)
                self.assertEqual((event.event, event.revision), ('reset', 3))

    def test_gap_does_not_discard_other_clients_valid_replay(self):
        reader = Mock(side_effect=[result(5, reset=True), result(5, events=(4, 5))])
        hub = self.hub(reader)
        old, recent = hub.subscribe('0'), hub.subscribe('3')
        hub.poll_once()
        self.assertEqual(old.next_event(0).event, 'reset')
        self.assertIsNone(recent.next_event(0))
        hub.poll_once()
        self.assertEqual([recent.next_event(0).revision for _ in range(2)], [4, 5])
        self.assertEqual([call.args for call in reader.call_args_list], [(0,), (3,)])

    def test_database_replacement_can_reset_to_lower_revision(self):
        hub = self.hub(Mock(return_value=result(2, reset=True)))
        subscriber = hub.subscribe('97')
        hub.poll_once()
        self.assertEqual(subscriber.next_event(0).revision, 2)

    def test_slow_viewer_queue_overflow_resets_and_stays_bounded(self):
        reader = Mock(side_effect=[result(2, events=(1, 2)), result(4, events=(3, 4))])
        hub = self.hub(reader, max_queue=2)
        subscriber = hub.subscribe('0')
        hub.poll_once()
        hub.poll_once()
        self.assertLessEqual(subscriber.pending_count, 2)
        event = subscriber.next_event(0)
        self.assertEqual((event.event, event.revision), ('reset', 4))
        self.assertIsNone(subscriber.next_event(0))

    def test_heartbeat_refreshes_freshness_without_inventing_revision_or_timestamp(self):
        reader = Mock(side_effect=[result(1, reset=True), result(1, state='stale'), RuntimeError('secret')])
        hub = self.hub(reader)
        subscriber = hub.subscribe(None)
        hub.poll_once()
        subscriber.next_event(0)
        hub.poll_once()
        heartbeat = hub.heartbeat()
        self.assertIsNone(heartbeat.revision)
        self.assertEqual(heartbeat.data['revision'], 1)
        self.assertEqual(heartbeat.data['freshness']['state'], 'stale')
        self.assertEqual(heartbeat.data['freshness']['last_success_at'], snapshot()['last_success_at'])
        hub.poll_once()
        unavailable = hub.heartbeat()
        self.assertEqual(unavailable.data['stream']['state'], 'unavailable')
        self.assertEqual(unavailable.data['freshness'], {
            **heartbeat.data['freshness'], 'state': 'unavailable',
        })
        self.assertNotIn('secret', unavailable.encode())

    def test_database_outage_fails_closed_and_same_revision_recovery_restores_games(self):
        latest = result(1, reset=True)
        latest['snapshot']['games'] = [{'source_event_id': 'balldontlie:77'}]
        reader = Mock(side_effect=[latest, RuntimeError('offline'),
                                   {**latest, 'reset': False}])
        hub = self.hub(reader)
        subscriber = hub.subscribe(None)
        hub.poll_once()
        self.assertEqual(subscriber.pending_count, 1)
        hub.poll_once()
        self.assertEqual(subscriber.pending_count, 0)
        heartbeat = hub.heartbeat().data
        self.assertEqual(heartbeat['freshness']['state'], 'unavailable')
        self.assertEqual(heartbeat['freshness']['last_success_at'],
                         latest['snapshot']['last_success_at'])
        self.assertEqual(heartbeat['revision'], 1)
        hub.poll_once()
        recovered = subscriber.next_event(0)
        self.assertIsNotNone(recovered)
        self.assertEqual((recovered.event, recovered.revision), ('reset', 1))
        self.assertEqual(recovered.data['games'], latest['snapshot']['games'])
        self.assertEqual(hub.heartbeat().data['freshness']['state'], 'current')

    def test_slots_are_capped_and_close_is_idempotent(self):
        hub = self.hub(Mock(), max_clients=2)
        first, second = hub.subscribe(None), hub.subscribe(None)
        with self.assertRaises(StreamCapacityError):
            hub.subscribe(None)
        first.close()
        first.close()
        third = hub.subscribe(None)
        self.assertEqual(hub.client_count, 2)
        second.close()
        third.close()
        hub.poll_once()
        hub.reader.assert_not_called()

    def test_lifetime_and_generator_close_release_slots(self):
        clock = Mock(return_value=0.0)
        hub = self.hub(Mock(), monotonic=clock, max_lifetime_seconds=300)
        subscriber = hub.subscribe(None)
        stream = subscriber.iter_sse()
        self.assertEqual(next(stream), 'retry: 3000\n\n')
        clock.return_value = 301.0
        with self.assertRaises(StopIteration):
            next(stream)
        self.assertEqual(hub.client_count, 0)
        subscriber = hub.subscribe(None)
        stream = subscriber.iter_sse()
        next(stream)
        stream.close()
        self.assertEqual(hub.client_count, 0)

    def test_idle_stream_emits_heartbeat_and_background_reader_stops_when_idle(self):
        read = threading.Event()
        reader = Mock(side_effect=lambda cursor: (read.set() or result(1, reset=True)))
        hub = FeedStreamHub(reader, poll_seconds=60, heartbeat_seconds=0.01)
        self.addCleanup(hub.close)
        subscriber = hub.subscribe(None)
        self.assertTrue(read.wait(1))
        stream = subscriber.iter_sse()
        next(stream)
        self.assertIn('event: reset\n', next(stream))
        heartbeat = next(stream)
        self.assertIn('event: heartbeat\n', heartbeat)
        self.assertNotIn('\nid:', heartbeat)
        stream.close()
        reader.assert_called_once()

    def test_reader_error_recovers_without_ending_subscribers(self):
        reader = Mock(side_effect=[RuntimeError('offline'), result(4, reset=True)])
        hub = self.hub(reader)
        subscriber = hub.subscribe(None)
        hub.poll_once()
        self.assertEqual(hub.heartbeat().data['stream']['state'], 'unavailable')
        self.assertFalse(subscriber.closed)
        hub.poll_once()
        self.assertEqual(subscriber.next_event(0).revision, 4)
        self.assertEqual(hub.heartbeat().data['stream']['state'], 'connected')


class FeedStreamRouteTests(BaseTestCase):
    def test_both_endpoints_require_login(self):
        for path in ('/nba/live-feed', '/nba/live-events'):
            self.assertEqual(self.client.get(path).status_code, 302)

    def test_snapshot_envelope_and_private_cache_headers(self):
        self.register_and_login()
        with patch('app.routes.live_feed.get_feed_snapshot', return_value=snapshot()) as reader:
            response = self.client.get('/nba/live-feed')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json(), {'ok': True, 'data': snapshot(), 'error': None, 'meta': {}})
        self.assertIn('no-store', response.headers['Cache-Control'])
        reader.assert_called_once_with()

    def test_snapshot_database_error_is_safe_retryable_envelope(self):
        self.register_and_login()
        with patch('app.routes.live_feed.get_feed_snapshot', side_effect=RuntimeError('private DSN')):
            response = self.client.get('/nba/live-feed')
        self.assertEqual(response.status_code, 503)
        self.assertFalse(response.get_json()['ok'])
        self.assertEqual(response.get_json()['error']['details'], {})
        self.assertNotIn('private DSN', response.get_data(as_text=True))
        self.assertEqual(response.headers['Retry-After'], '15')

    def test_stream_header_cursor_priority_headers_and_connection_release(self):
        self.register_and_login()
        hub = FeedStreamHub(Mock(), start_thread=False)
        self.addCleanup(hub.close)
        with patch('app.routes.live_feed.get_feed_hub', return_value=hub), patch.object(hub, 'subscribe', wraps=hub.subscribe) as subscribe:
            with patch('app.routes.live_feed.db.session.remove', wraps=__import__('app').db.session.remove) as remove:
                response = self.client.get('/nba/live-events?last_event_id=1', headers={'Last-Event-ID': '2'}, buffered=False)
                self.assertGreaterEqual(remove.call_count, 1)
            subscribe.assert_called_once_with('2')
            self.assertEqual(response.mimetype, 'text/event-stream')
            self.assertEqual(response.headers['X-Accel-Buffering'], 'no')
            self.assertIn('no-store', response.headers['Cache-Control'])
            self.assertEqual(hub.client_count, 1)
            response.close()
            self.assertEqual(hub.client_count, 0)

    def test_query_cursor_and_capacity_retry_after(self):
        self.register_and_login()
        hub = FeedStreamHub(Mock(), start_thread=False, max_clients=1)
        self.addCleanup(hub.close)
        with patch('app.routes.live_feed.get_feed_hub', return_value=hub), patch.object(hub, 'subscribe', wraps=hub.subscribe) as subscribe:
            response = self.client.get('/nba/live-events?last_event_id=7', buffered=False)
            subscribe.assert_called_once_with('7')
            rejected = self.client.get('/nba/live-events', buffered=False)
            self.assertEqual(rejected.status_code, 429)
            self.assertEqual(rejected.headers['Retry-After'], '15')
            self.assertFalse(rejected.get_json()['ok'])
            response.close()

    def test_gunicorn_reserves_threads_with_single_worker_default(self):
        with patch.dict('os.environ', {}, clear=True):
            config = runpy.run_path(str(Path(__file__).resolve().parents[1] / 'gunicorn.conf.py'))
        self.assertEqual(config['workers'], 1)
        self.assertEqual(config['worker_class'], 'gthread')
        self.assertGreaterEqual(config['threads'], 16)

    def test_real_db_two_open_streams_share_read_and_release_connections_without_provider(self):
        from app.services.basketball_feed import read_feed_events
        from app.services.feed_stream import get_feed_hub

        self.register_and_login()
        self.app.config['BALLDONTLIE_API_KEY'] = ''
        reader = Mock(wraps=read_feed_events)
        hub = FeedStreamHub(reader, app=self.app, start_thread=False)
        self.addCleanup(hub.close)
        self.app.extensions['nba_feed_hub'] = hub
        self.assertIs(get_feed_hub(self.app), hub)
        self.assertIs(get_feed_hub(self.app), hub)
        with self.app.app_context():
            engine = db.engine
        connections = {'active': 0}

        def checkout(*args):
            connections['active'] += 1

        def checkin(*args):
            connections['active'] -= 1

        event.listen(engine, 'checkout', checkout)
        event.listen(engine, 'checkin', checkin)
        self.addCleanup(event.remove, engine, 'checkout', checkout)
        self.addCleanup(event.remove, engine, 'checkin', checkin)
        with patch('app.services.basketball_feed.requests.get') as provider:
            first = self.client.get('/nba/live-events', buffered=False)
            second = self.client.get('/nba/live-events', buffered=False)
            try:
                self.assertEqual(connections['active'], 0)
                self.assertEqual(next(first.response), b'retry: 3000\n\n')
                self.assertEqual(next(second.response), b'retry: 3000\n\n')
                hub.poll_once()
                self.assertEqual(connections['active'], 0)
                first_event = next(first.response)
                self.assertEqual(first_event, next(second.response))
                self.assertIn(b'event: reset\n', first_event)
                self.assertIn(b'"state":"disabled"', first_event)
                reader.assert_called_once_with(None)
                self.assertEqual(self.client.get('/nba/live-feed').status_code, 200)
                provider.assert_not_called()
            finally:
                first.close()
                second.close()
            self.assertEqual(hub.client_count, 0)

    def test_collector_updates_are_identical_for_two_clients_without_extra_provider_fetches(self):
        from app.services.basketball_feed import collect_basketball_feed, read_feed_events
        from tests.test_basketball_feed import NOW, game, response as provider_response

        self.register_and_login()
        self.app.config['BALLDONTLIE_API_KEY'] = 'fixture-only'
        clock = {'now': NOW}
        reader = Mock(side_effect=lambda cursor: read_feed_events(cursor, now=clock['now']))
        hub = FeedStreamHub(reader, app=self.app, start_thread=False)
        self.addCleanup(hub.close)
        self.app.extensions['nba_feed_hub'] = hub
        with patch('app.services.basketball_feed.requests.get', return_value=provider_response()) as provider:
            with self.app.app_context():
                collect_basketball_feed(now=clock['now'])
            first = self.client.get('/nba/live-events', buffered=False)
            second = self.client.get('/nba/live-events', buffered=False)
            try:
                next(first.response)
                next(second.response)
                hub.poll_once()
                initial = next(first.response)
                self.assertEqual(initial, next(second.response))
                self.assertIn(b'"score":10', initial)
                self.assertEqual(provider.call_count, 1)
                clock['now'] += timedelta(seconds=30)
                provider.return_value = provider_response([game(score=12)])
                with self.app.app_context():
                    collect_basketball_feed(now=clock['now'])
                hub.poll_once()
                update = next(first.response)
                self.assertEqual(update, next(second.response))
                self.assertIn(b'event: snapshot\n', update)
                self.assertIn(b'"score":12', update)
                self.assertEqual(provider.call_count, 2)
                self.assertEqual(reader.call_count, 2)
            finally:
                first.close()
                second.close()


if __name__ == '__main__':
    unittest.main()
