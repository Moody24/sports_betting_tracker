"""Freshness behavior under provider failures, ET rollover, and quote changes."""

import json
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from flask import Flask

from app.services import game_day_coordinator as coordinator
from app.services import nba_service, score_cache
from app.services.espn_client import EspnClientError
from app.utils.time_helpers import ET
from tests.helpers import BaseTestCase


class PatchedTestCase(unittest.TestCase):
    def enter_context(self, patcher):
        value = patcher.start()
        self.addCleanup(patcher.stop)
        return value


class ScoreboardFreshnessTests(PatchedTestCase):
    def setUp(self):
        nba_service._GAMES_CACHE.clear()
        coordinator._DAY_CACHE.clear()
        self.addCleanup(nba_service._GAMES_CACHE.clear)
        self.addCleanup(coordinator._DAY_CACHE.clear)

    @patch('app.services.nba_service.fetch_scoreboard_payload')
    def test_strict_scoreboard_distinguishes_failure_from_empty_success(self, fetch):
        fetch.side_effect = EspnClientError('unavailable')
        self.assertEqual(nba_service.fetch_espn_scoreboard(), [])
        with self.assertRaises(EspnClientError):
            nba_service.fetch_espn_scoreboard(raise_on_error=True)
        fetch.side_effect = None
        fetch.return_value = {'events': []}
        self.assertEqual(nba_service.fetch_espn_scoreboard(raise_on_error=True), [])

    @patch('app.services.nba_service.fetch_scoreboard_payload', return_value={})
    def test_missing_events_is_not_a_verified_empty_slate(self, _fetch):
        with self.assertRaises(EspnClientError):
            nba_service.fetch_espn_scoreboard(raise_on_error=True)

    @patch('app.services.nba_service.fetch_odds_events', return_value={})
    @patch('app.services.nba_service.fetch_odds_combined', return_value=({}, {}, {}))
    @patch('app.services.nba_service.fetch_scoreboard_payload')
    def test_game_cache_does_not_store_provider_failure(self, fetch, _odds, _events):
        fetch.side_effect = [EspnClientError('unavailable'), {'events': []}]
        self.assertEqual(nba_service.get_todays_games(), [])
        self.assertFalse(nba_service._GAMES_CACHE)
        self.assertEqual(nba_service.get_todays_games(), [])
        self.assertEqual(fetch.call_count, 2)
        self.assertTrue(nba_service._GAMES_CACHE)
        _odds.assert_not_called()
        _events.assert_not_called()

    @patch('app.services.game_day_coordinator._catch_up_lookback', return_value=0)
    @patch('app.services.game_day_coordinator.fetch_espn_scoreboard')
    def test_failed_initial_read_is_retried_on_next_tick(self, fetch, catch_up):
        now = datetime(2026, 11, 6, 9, tzinfo=ET)
        fetch.side_effect = [EspnClientError('unavailable'), []]
        with self.assertRaises(EspnClientError):
            coordinator.run_tick(now)
        self.assertFalse(coordinator._DAY_CACHE)
        self.assertEqual(coordinator.run_tick(now + timedelta(minutes=5)), 'dormant')
        self.assertEqual(fetch.call_count, 2)
        catch_up.assert_called_once()

    @patch('app.services.game_day_coordinator._catch_up_lookback', return_value=0)
    @patch('app.services.game_day_coordinator.fetch_espn_scoreboard')
    def test_empty_day_rechecks_and_detects_added_game(self, fetch, catch_up):
        now = datetime(2026, 11, 6, 9, tzinfo=ET)
        fetch.side_effect = [[], [{
            'espn_id': 'game-1', 'status': 'STATUS_SCHEDULED',
            'start_time': '2026-11-07T00:00:00Z',
        }]]
        self.assertEqual(coordinator.run_tick(now), 'dormant')
        self.assertEqual(coordinator.run_tick(now + timedelta(minutes=5)), 'dormant')
        self.assertEqual(fetch.call_count, 1)
        self.assertEqual(coordinator.run_tick(now + timedelta(minutes=15)), 'pre-game')
        catch_up.assert_called_once()

    @patch('app.services.game_day_coordinator._catch_up_lookback', return_value=0)
    @patch('app.services.game_day_coordinator.fetch_espn_scoreboard')
    def test_failed_empty_day_recheck_keeps_original_retry_deadline(self, fetch, _catch_up):
        now = datetime(2026, 11, 6, 9, tzinfo=ET)
        fetch.side_effect = [[], EspnClientError('unavailable'), []]
        coordinator.run_tick(now)
        with self.assertRaises(EspnClientError):
            coordinator.run_tick(now + timedelta(minutes=15))
        self.assertEqual(coordinator._DAY_CACHE[now.date()]['checked_at'], now)
        self.assertEqual(coordinator.run_tick(now + timedelta(minutes=20)), 'dormant')
        self.assertEqual(fetch.call_count, 3)

    @patch('app.services.game_day_coordinator._catch_up_lookback', return_value=0)
    @patch('app.services.game_day_coordinator.fetch_espn_scoreboard', return_value=[])
    def test_coordinator_uses_et_date_for_aware_utc_input(self, _fetch, _catch_up):
        now = datetime(2026, 11, 7, 2, tzinfo=timezone.utc)
        coordinator.run_tick(now)
        self.assertIn(now.astimezone(ET).date(), coordinator._DAY_CACHE)
        self.assertNotIn(now.date(), coordinator._DAY_CACHE)


class ScoreboardStatusTests(PatchedTestCase):
    def setUp(self):
        nba_service._SCOREBOARD_STATUS.clear()
        self.addCleanup(nba_service._SCOREBOARD_STATUS.clear)
        self.now = datetime(2026, 11, 6, 23, 59, tzinfo=ET)
        self.clock = self.enter_context(patch(
            'app.services.nba_service.datetime', wraps=datetime,
        ))
        self.clock.now.return_value = self.now
        self.fetch = self.enter_context(patch(
            'app.services.nba_service.fetch_scoreboard_payload', return_value={'events': []},
        ))

    def test_read_has_no_network_and_empty_success_is_fresh(self):
        self.assertEqual(nba_service.get_scoreboard_status()['state'], 'unavailable')
        self.fetch.assert_not_called()
        nba_service.fetch_espn_scoreboard()
        self.fetch.assert_called_once_with('20261106')
        status = nba_service.get_scoreboard_status()
        self.assertEqual(status['state'], 'fresh')
        self.assertEqual(status['last_success_at'], self.now.isoformat())
        self.assertEqual(status['age_seconds'], 0)

    def test_failure_preserves_success_timestamp_and_marks_stale(self):
        nba_service.fetch_espn_scoreboard()
        self.clock.now.return_value = self.now + timedelta(seconds=20)
        self.fetch.side_effect = EspnClientError('unavailable secret-provider-detail')
        nba_service.fetch_espn_scoreboard()
        status = nba_service.get_scoreboard_status()
        self.assertEqual(status['state'], 'stale')
        self.assertEqual(status['last_success_at'], self.now.isoformat())
        self.assertEqual(status['last_attempt_at'], self.clock.now.return_value.isoformat())
        self.assertEqual(status['age_seconds'], 20)
        self.assertEqual(status['error_code'], 'scoreboard_unavailable')
        self.assertNotIn('secret-provider-detail', str(status))

    def test_other_dates_and_et_rollover_do_not_claim_today_is_fresh(self):
        nba_service.fetch_espn_scoreboard('20261107')
        self.assertEqual(nba_service.get_scoreboard_status()['state'], 'unavailable')
        nba_service.fetch_espn_scoreboard()
        self.clock.now.return_value = self.now + timedelta(minutes=2)
        status = nba_service.get_scoreboard_status()
        self.assertEqual(status['state'], 'unavailable')
        self.assertEqual(status['day'], '2026-11-07')
        self.assertIsNone(status['last_success_at'])
        self.assertIsNone(status['last_attempt_at'])

    def test_success_ages_to_stale_without_refreshing_timestamp(self):
        self.clock.now.return_value = self.now - timedelta(minutes=10)
        nba_service.fetch_espn_scoreboard()
        self.clock.now.return_value += timedelta(seconds=61)
        self.assertEqual(nba_service.get_scoreboard_status()['state'], 'stale')
        self.assertEqual(self.fetch.call_count, 1)


    def test_fall_back_hour_uses_elapsed_time_for_freshness(self):
        self.clock.now.return_value = datetime(2026, 11, 1, 1, 30, tzinfo=ET, fold=0)
        nba_service.fetch_espn_scoreboard()
        self.clock.now.return_value = datetime(2026, 11, 1, 1, 15, tzinfo=ET, fold=1)
        status = nba_service.get_scoreboard_status()
        self.assertEqual(status['age_seconds'], 45 * 60)
        self.assertEqual(status['state'], 'stale')


class ScoreCacheFreshnessTests(PatchedTestCase):
    def setUp(self):
        score_cache.invalidate_scores()
        self.addCleanup(score_cache.invalidate_scores)
        self.clock = self.enter_context(patch(
            'app.services.score_cache._time.monotonic', return_value=100.0,
        ))
        self.day = self.enter_context(patch(
            'app.services.score_cache.et_date_str', return_value='2026-11-06',
        ))
        self.games = self.enter_context(patch(
            'app.services.nba_service.get_todays_games', return_value=[],
        ))
        self.enter_context(patch('app.services.projection_engine.ProjectionEngine'))
        self.detector = self.enter_context(patch('app.services.value_detector.ValueDetector'))
        self.score = self.detector.return_value.score_all_todays_props
        self.score.return_value = [{
            'player': 'Player A', 'breakdown': {'value': 1},
            'quote_source': 'odds_api',
            'quote_observed_at': datetime.now(timezone.utc).isoformat(),
        }]

    def test_refresh_bypasses_detector_cache_and_owns_result(self):
        scores = score_cache.get_todays_scores()
        scores[0]['breakdown']['value'] = 999
        self.score.assert_called_once_with(games=[])
        self.games.assert_called_once_with(raise_on_error=True)
        self.assertEqual(score_cache.get_todays_scores()[0]['breakdown']['value'], 1)

    def test_failed_refresh_does_not_reset_age_or_serve_stale_indefinitely(self):
        original = score_cache.get_todays_scores()
        self.clock.return_value = 401.0
        self.score.side_effect = RuntimeError('scoring failed')
        self.assertEqual(score_cache.get_todays_scores(), original)
        self.assertEqual(score_cache.peek_age(), 301.0)
        self.clock.return_value = 410.0
        self.assertEqual(score_cache.get_todays_scores(), original)
        self.assertEqual(self.score.call_count, 2)  # short failure backoff
        self.clock.return_value = 701.0
        self.assertEqual(score_cache.get_todays_scores(), [])
        self.assertEqual(score_cache.peek_age(), 601.0)

    def test_previous_et_day_is_never_used_as_error_fallback(self):
        score_cache.get_todays_scores()
        self.day.return_value = '2026-11-07'
        self.score.side_effect = RuntimeError('scoring failed')
        self.assertEqual(score_cache.get_todays_scores(), [])
        self.assertIsNone(score_cache.peek_age())

    def test_successful_empty_slate_replaces_previous_scores(self):
        score_cache.get_todays_scores()
        self.clock.return_value = 401.0
        self.score.return_value = []
        self.assertEqual(score_cache.get_todays_scores(), [])
        self.assertEqual(score_cache.peek_age(), 0.0)

    def test_replayed_archived_result_cannot_advance_success_timestamp(self):
        self.clock.return_value = 1000.0
        score_cache.get_todays_scores()
        self.clock.return_value = 1700.0
        self.score.return_value = [{
            'player': 'Fixture', 'quote_source': 'snapshot_fallback',
            'quote_observed_at': datetime.now(timezone.utc).isoformat(),
        }]
        self.assertEqual(score_cache.get_todays_scores(), [])
        self.assertEqual(score_cache._cache['ts'], 1000.0)
        self.assertEqual(score_cache.peek_age(), 700.0)

    def test_provider_quote_expiry_is_checked_even_before_cache_ttl(self):
        now = datetime.now(timezone.utc)
        self.score.return_value[0]['quote_observed_at'] = (now - timedelta(minutes=9)).isoformat()
        with patch('app.services.value_detector.datetime', wraps=datetime) as clock:
            clock.now.return_value = now
            self.assertEqual(len(score_cache.get_todays_scores()), 1)
            clock.now.return_value = now + timedelta(seconds=61)
            self.clock.return_value = 161.0
            self.assertEqual(score_cache.get_todays_scores(), [])
        self.score.assert_called_once()

    def test_run_crossing_et_midnight_does_not_return_previous_slate(self):
        self.day.side_effect = ['2026-11-06', '2026-11-07']
        self.assertEqual(score_cache.get_todays_scores(), [])
        self.assertIsNone(score_cache._cache['scores'])


class CurrentPropCacheTests(PatchedTestCase):
    def setUp(self):
        nba_service._PLAYER_PROPS_CACHE.clear()
        self.addCleanup(nba_service._PLAYER_PROPS_CACHE.clear)
        self.day = self.enter_context(patch(
            'app.services.nba_service.et_date_str', return_value='2026-11-06',
        ))
        self.clock = self.enter_context(patch(
            'app.services.nba_service._time.monotonic', return_value=100.0,
        ))
        self.fetch = self.enter_context(patch(
            'app.services.nba_service.fetch_player_props_for_event',
            return_value={'player_points': [{
                'player': 'Player A', 'line': 20.5,
                'source_last_update': datetime.now(timezone.utc).isoformat(),
            }]},
        ))
        self.games = [{'espn_id': 'game-1', 'odds_event_id': 'odds-1'}]

    def props(self):
        return nba_service.get_player_props('game-1', self.games, use_cache=True)

    def test_current_quotes_refresh_after_ttl_and_are_copy_isolated(self):
        first = self.props()
        first['player_points'][0]['line'] = 999
        self.assertEqual(self.props()['player_points'][0]['line'], 20.5)
        self.fetch.assert_called_once_with('odds-1')
        self.clock.return_value = 161.0
        self.fetch.return_value = {'player_points': [{
            'line': 22.5, 'source_last_update': datetime.now(timezone.utc).isoformat(),
        }]}
        self.assertEqual(self.props()['player_points'][0]['line'], 22.5)
        self.assertEqual(self.fetch.call_count, 2)

    def test_consensus_does_not_borrow_timestamp_from_other_selected_book(self):
        now = datetime.now(timezone.utc)
        books = {
            'fanduel': {'line': 20.5, 'over_odds': -100, 'under_odds': -120},
            'draftkings': {'line': 20.5, 'over_odds': -110, 'under_odds': -105,
                           'last_update': now.isoformat()},
        }
        prop = nba_service._consensus_prop('Player A', books)
        self.assertEqual(prop['best_over_book'], 'fanduel')
        self.assertEqual(prop['best_under_book'], 'draftkings')
        self.assertIsNone(prop['source_last_update'])

    def test_browser_quote_cache_does_not_accept_stale_provider_observations(self):
        self.fetch.return_value['player_points'][0]['source_last_update'] = (
            datetime.now(timezone.utc) - timedelta(minutes=11)
        ).isoformat()
        self.assertEqual(self.props(), {})
        self.assertFalse(nba_service._PLAYER_PROPS_CACHE)

    def test_expired_quotes_are_not_served_when_provider_returns_empty(self):
        self.props()
        self.clock.return_value = 161.0
        self.fetch.return_value = {}
        self.assertEqual(self.props(), {})
        self.assertFalse(nba_service._PLAYER_PROPS_CACHE)
        self.props()
        self.assertEqual(self.fetch.call_count, 3)

    def test_et_rollover_and_changed_event_identity_invalidate_quote(self):
        self.props()
        self.day.return_value = '2026-11-07'
        self.props()
        self.games[0]['odds_event_id'] = 'odds-2'
        self.props()
        self.assertEqual(self.fetch.call_count, 3)
        self.fetch.assert_called_with('odds-2')


class PropRouteFreshnessTests(PatchedTestCase):
    def setUp(self):
        self.app = Flask(__name__)
        self.snapshot = SimpleNamespace(
            is_final=False,
            props_json=json.dumps({'player_points': [{'line': 18.5}]}),
        )
        model = MagicMock()
        model.query.filter_by.return_value.first.return_value = self.snapshot
        self.enter_context(patch('app.routes.nba_live.GameSnapshot', model))
        self.fetch = self.enter_context(patch('app.routes.nba_live.get_player_props'))

    def props(self):
        from app.routes.nba_live import nba_props
        with self.app.test_request_context('/nba/props/game-1'):
            return nba_props.__wrapped__('game-1').get_json()

    def test_active_game_fetches_current_quote_instead_of_locked_snapshot(self):
        self.fetch.return_value = {'player_points': [{'line': 22.5}]}
        self.assertEqual(self.props(), self.fetch.return_value)
        self.fetch.assert_called_once_with('game-1', use_cache=True)

    def test_active_game_failure_does_not_present_archive_as_current(self):
        self.fetch.return_value = {}
        self.assertEqual(self.props(), {})

    def test_final_game_retains_archived_snapshot(self):
        self.snapshot.is_final = True
        self.assertEqual(self.props(), {'player_points': [{'line': 18.5}]})
        self.fetch.assert_not_called()


class PropSnapshotIdempotencyTests(BaseTestCase):
    def setUp(self):
        super().setUp()
        self.observed_at = datetime(2026, 11, 6, 23, tzinfo=timezone.utc)
        self.quote = {
            'line': 20.5, 'over_odds': -110, 'under_odds': -110,
            'last_update': self.observed_at.isoformat(),
        }
        self.identity = patch('app.services.player_crosswalk.resolve_espn_id', return_value='player-1')
        self.identity.start()
        self.addCleanup(self.identity.stop)

    def capture(self, kind='decision'):
        return nba_service._snapshot_prop_book_rows(
            prop={'player': 'Player A', 'line': 20.5, 'books': {'fanduel': self.quote}},
            market_key='player_points', game_id='game-1', event_id='odds-1',
            event_start=self.observed_at + timedelta(hours=1),
            today=self.observed_at.astimezone(ET).date(), snapshot_kind=kind,
            recent_snapped=set(), observed_at=self.observed_at,
        )

    def test_replayed_payload_deduplicates_after_advisory_cache_is_lost(self):
        from app import db
        from app.models import OddsSnapshot
        with self.app.app_context():
            self.assertEqual(self.capture(), 1)
            db.session.commit()
            self.observed_at += timedelta(seconds=30)
            self.assertEqual(self.capture(), 0)
            self.assertEqual(OddsSnapshot.query.count(), 1)
            self.assertTrue(OddsSnapshot.query.one().source_snapshot_key.startswith('odds_api:'))
            self.assertEqual(self.capture(kind='close'), 1)
            db.session.commit()
            self.assertEqual(OddsSnapshot.query.count(), 2)

    def test_unique_constraint_handles_race_without_aborting_transaction(self):
        from app import db
        from app.models import OddsSnapshot
        with self.app.app_context():
            self.capture()
            db.session.commit()
            existing = OddsSnapshot.query.one()
            query = MagicMock()
            query.filter_by.return_value.first.side_effect = [None, existing]
            with patch.object(OddsSnapshot, 'query', query):
                self.assertEqual(self.capture(), 0)
            db.session.commit()
            self.assertEqual(OddsSnapshot.query.count(), 1)

    def test_unverified_observation_time_is_not_persisted(self):
        from app.models import OddsSnapshot
        with self.app.app_context():
            for invalid_time in (None, '2026-11-06T23:00:00', '2026-11-07T00:00:00Z',
                                 '2026-10-06T23:00:00Z', '2026-11-06T22:49:59Z'):
                self.quote['last_update'] = invalid_time
                self.assertEqual(self.capture(), 0)
            self.assertEqual(OddsSnapshot.query.count(), 0)


class DailyQuoteFreshnessTests(BaseTestCase):
    def test_provider_failure_never_scores_stored_snapshot_as_current(self):
        from app import db
        from app.models import GameSnapshot
        from app.services.value_detector import CurrentQuotesUnavailable, ValueDetector
        game = {'espn_id': 'game-1', 'odds_event_id': 'odds-1'}
        with self.app.app_context():
            db.session.add(GameSnapshot(
                espn_id='game-1', game_date=datetime.now(ET).date(),
                home_team='Home', away_team='Away',
                props_json=json.dumps({'player_points': [{
                    'player': 'Player A', 'line': 20.5,
                    'source_last_update': (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat(),
                }]}),
            ))
            db.session.commit()
            detector = ValueDetector(MagicMock())
            with patch('app.services.nba_service.fetch_player_props_for_event', return_value={}), \
                 patch.object(detector, '_fill_snapshot_prop_fallbacks') as archive, \
                 patch.object(detector, 'score_prop') as score:
                with self.assertRaises(CurrentQuotesUnavailable):
                    detector.score_all_todays_props(games=[game])
            archive.assert_not_called()
            score.assert_not_called()
            self.assertIsNotNone(GameSnapshot.query.one().props_json)

    def test_current_payload_boundary_rejects_missing_stale_and_future_quotes(self):
        from app.services.value_detector import ValueDetector
        now = datetime.now(timezone.utc)
        dates = [None, (now - timedelta(minutes=11)).isoformat(),
                 (now + timedelta(minutes=1)).isoformat(), now.isoformat()]
        props = {'player_points': [{
            'player': str(index), 'source_last_update': observed,
        } for index, observed in enumerate(dates)]}
        payloads, players = ValueDetector._collect_prop_payloads([
            ({'props_source': 'odds_api'}, props),
            ({'props_source': 'snapshot_fallback'}, props),
        ])
        self.assertEqual(players, {'3'})
        self.assertEqual(len(payloads), 1)

    def test_actionable_filter_and_detector_cache_recheck_source_age(self):
        from app.services import value_detector
        now = datetime.now(timezone.utc)
        fresh = {
            'edge': 0.2, 'confidence_tier': 'strong', 'games_played': 30,
            'quote_source': 'odds_api', 'quote_observed_at': now.isoformat(),
        }
        rows = [fresh, {**fresh, 'quote_source': 'snapshot_fallback'},
                {**fresh, 'quote_observed_at': None},
                {**fresh, 'quote_observed_at': (now - timedelta(minutes=11)).isoformat()}]
        self.assertEqual(value_detector.ValueDetector.filter_plays(rows), [fresh])
        with patch.dict(value_detector._SCORE_CACHE, {}, clear=True):
            value_detector.ValueDetector._cache_daily_scores(rows)
            self.assertEqual(value_detector.ValueDetector._cached_daily_scores(), [fresh])


class CurrentBuilderQuoteTests(BaseTestCase):
    @patch('app.routes.nba_analysis.get_todays_games', return_value=[])
    @patch('app.routes.nba_analysis.fetch_upcoming_games', return_value=[])
    def test_unavailable_current_feed_does_not_return_archived_builder_lines(self, _upcoming, _games):
        from app import db
        from app.models import OddsSnapshot
        self.register_and_login()
        with self.app.app_context():
            db.session.add(OddsSnapshot(
                game_id='game-1', game_date=datetime.now(ET).date(),
                player_name='Archived Player', market='player_points',
                bookmaker='fanduel', line=20.5, over_odds=-110, under_odds=-110,
                snapped_at=datetime.now(timezone.utc) - timedelta(hours=1),
            ))
            db.session.commit()
        response = self.client.get('/nba/all-props')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json(), [])
        self.assertEqual(response.headers['X-Prop-Data-State'], 'unavailable')
        with self.app.app_context():
            self.assertEqual(OddsSnapshot.query.count(), 1)

    def test_live_builder_keeps_quote_provenance_and_rejects_undated_or_old_lines(self):
        from app.routes.nba_analysis import _live_props_for_games
        now = datetime.now(timezone.utc)
        props = {'player_points': [
            {'player': 'Fresh Player', 'line': 20.5, 'source_last_update': now.isoformat()},
            {'player': 'Old Player', 'line': 25.5,
             'source_last_update': (now - timedelta(minutes=11)).isoformat()},
            {'player': 'Undated Player', 'line': 30.5},
        ]}
        with patch('app.routes.nba_analysis.fetch_player_props_for_event', return_value=props):
            lines, players = _live_props_for_games([{
                'odds_event_id': 'event-1', 'status': 'STATUS_SCHEDULED',
                'start_time': now.isoformat(), 'home': {'name': 'Home'}, 'away': {'name': 'Away'},
            }])
        self.assertEqual(players, {'Fresh Player'})
        self.assertEqual(len(lines), 1)
        self.assertEqual(lines[0]['quote_source'], 'odds_api')
        self.assertEqual(lines[0]['quote_observed_at'], now.isoformat())


class UpcomingIdentityAndCurrentMarketsTests(unittest.TestCase):
    def test_upcoming_odds_identity_stays_separate_and_date_uses_et(self):
        for commence, et_day in [
            ('2026-10-09T00:30:00Z', '2026-10-08'),
            ('2026-11-02T01:30:00Z', '2026-11-01'),
        ]:
            with self.subTest(commence=commence):
                tomorrow = datetime.fromisoformat(et_day).date()
                game = nba_service._normalize_upcoming_odds_game({
                    'id': 'odds-event', 'commence_time': commence,
                    'home_team': 'Home', 'away_team': 'Away',
                }, tomorrow)
                self.assertEqual(game['match_date'], et_day)
                self.assertEqual(game['espn_id'], '')
                self.assertEqual(game['odds_event_id'], 'odds-event')
                self.assertEqual(game['selection_key'], 'odds_api:odds-event')

    def test_upcoming_rejects_unverifiable_or_outside_window_dates(self):
        tomorrow = datetime(2026, 10, 8).date()
        for commence in [None, '', 'invalid', '2026-10-08T19:00:00',
                         '2026-10-08T00:30:00Z', '2026-10-10T00:30:00Z']:
            with self.subTest(commence=commence):
                self.assertIsNone(nba_service._normalize_upcoming_odds_game({
                    'id': 'odds-event', 'commence_time': commence,
                }, tomorrow))

    def test_upcoming_fetch_omits_rejected_events(self):
        tomorrow = datetime(2026, 10, 8).date()
        with patch('app.services.nba_service._get_odds_api_key', return_value='fixture'), \
             patch('app.services.nba_service.ODDS_BUDGET.budgeted_get') as fetch:
            fetch.return_value.json.return_value = [
                {'id': 'bad', 'commence_time': '2026-10-08T19:00:00'},
                {'id': 'good', 'commence_time': '2026-10-09T00:30:00Z'},
            ]
            games = nba_service._fetch_upcoming_games_odds(tomorrow)
        self.assertEqual([game['odds_event_id'] for game in games], ['good'])

    def test_espn_fallback_keeps_espn_namespace(self):
        tomorrow = datetime(2026, 10, 8).date()
        with patch('app.services.nba_service.fetch_espn_scoreboard', return_value=[{
            'espn_id': 'espn-event', 'status': 'STATUS_SCHEDULED',
            'home': {'name': 'Home'}, 'away': {'name': 'Away'},
        }]):
            game = nba_service._fetch_upcoming_games_espn(tomorrow)[0]
        self.assertEqual(game['espn_id'], 'espn-event')
        self.assertEqual(game['selection_key'], 'espn:espn-event')
        self.assertEqual(game['odds_event_id'], '')

    @staticmethod
    def _market_game(observed):
        return {'home_team': 'Home', 'away_team': 'Away', 'bookmakers': [{
            'last_update': observed,
            'markets': [
                {'key': 'totals', 'outcomes': [{'name': 'Over', 'point': 220.5}]},
                {'key': 'h2h', 'outcomes': [
                    {'name': 'Home', 'price': -120}, {'name': 'Away', 'price': 110},
                ]},
                {'key': 'spreads', 'outcomes': [{'name': 'Home', 'point': -3.5}]},
            ],
        }]}

    def test_current_markets_require_fresh_aware_source_time_but_history_does_not(self):
        now = datetime.now(timezone.utc)
        for observed in [None, 'invalid', now.replace(tzinfo=None).isoformat(),
                         (now + timedelta(minutes=1)).isoformat(),
                         (now - timedelta(minutes=11)).isoformat()]:
            with self.subTest(observed=observed):
                game = self._market_game(observed)
                self.assertEqual(nba_service._extract_current_game_markets(game),
                                 (None, None, None, None))
                self.assertIsNone(nba_service._upcoming_total_line(game))
                self.assertEqual(nba_service._extract_market_lines_from_odds_game(game),
                                 (220.5, -120, 110))
        fresh = self._market_game((now - timedelta(minutes=9)).isoformat())
        self.assertEqual(nba_service._extract_current_game_markets(fresh),
                         (220.5, -120, 110, -3.5))
        self.assertEqual(nba_service._upcoming_total_line(fresh), 220.5)

    def test_market_timestamp_takes_precedence_over_bookmaker_timestamp(self):
        now = datetime.now(timezone.utc)
        game = self._market_game(now.isoformat())
        for market in game['bookmakers'][0]['markets']:
            market['last_update'] = (now - timedelta(days=30)).isoformat()
        self.assertEqual(nba_service._extract_current_game_markets(game),
                         (None, None, None, None))
        self.assertIsNone(nba_service._upcoming_total_line(game))


class UpcomingRouteIdentityTests(BaseTestCase):
    def test_picker_and_props_share_source_key_without_fabricating_espn_id(self):
        from app.routes.nba_analysis import _live_props_for_games
        self.register_and_login()
        tomorrow = datetime.now(ET).date() + timedelta(days=1)
        utc_start = datetime.combine(tomorrow, datetime.min.time(), tzinfo=ET) + timedelta(hours=20)
        game = nba_service._normalize_upcoming_odds_game({
            'id': 'odds-only', 'commence_time': utc_start.astimezone(timezone.utc).isoformat(),
            'home_team': 'Home', 'away_team': 'Away',
        }, tomorrow)
        today = {**game, 'espn_id': 'espn-today', 'odds_event_id': 'odds-today'}
        with patch('app.routes.nba_live.get_todays_games', return_value=[today]), \
             patch('app.routes.nba_live.fetch_upcoming_games', return_value=[game]):
            response = self.client.get('/nba/upcoming-games')
        self.assertEqual(response.status_code, 200)
        picker = response.get_json()
        self.assertEqual(picker[0]['game_id'], 'espn-today')
        self.assertEqual(picker[0]['selection_key'], 'espn:espn-today')
        self.assertEqual(picker[0]['match_date'], tomorrow.isoformat())
        self.assertEqual(picker[1]['game_id'], '')
        self.assertEqual(picker[1]['selection_key'], 'odds_api:odds-only')
        self.assertEqual(picker[1]['odds_event_id'], 'odds-only')
        with patch('app.routes.nba_analysis.fetch_player_props_for_event', return_value={
            'player_points': [{'player': 'Fixture Player', 'line': 20.5,
                               'source_last_update': datetime.now(timezone.utc).isoformat()}],
        }):
            props, _ = _live_props_for_games([game])
        self.assertEqual(props[0]['game_id'], '')
        self.assertEqual(props[0]['selection_key'], picker[1]['selection_key'])
        self.assertEqual(props[0]['match_date'], picker[1]['match_date'])
