"""Request validation must fail before provider calls or durable bet writes."""

from copy import deepcopy
import unittest
from unittest.mock import patch

import requests

from app.models import Bet
from app.services.espn_client import EspnClientError
from tests.helpers import BaseTestCase


class TestRequestValidation(BaseTestCase):
    def setUp(self):
        super().setUp()
        self.app.config['PROPAGATE_EXCEPTIONS'] = False
        self.register_and_login()

    @staticmethod
    def progress(**overrides):
        item = {
            'card_id': 'card-1', 'espn_id': 'game123', 'player': 'LeBron James',
            'prop_type': 'player_points', 'line': 25.5, 'bet_type': 'over',
        }
        item.update(overrides)
        return item

    @staticmethod
    def placement():
        return {
            'stake': 25, 'legs': [{
                'team_a': 'Lakers', 'team_b': 'Celtics', 'game_id': 'game123',
                'player_name': 'LeBron James', 'prop_type': 'player_points',
                'prop_line': 25.5, 'bet_type': 'over', 'american_odds': -110,
                'match_date': '2026-01-01',
            }],
        }

    def assert_invalid_progress(self, payload):
        with patch('app.routes.nba_live.fetch_summary_payload') as fetch:
            response = self.client.post('/nba/prop-progress/batch', json=payload)
        self.assertEqual(response.status_code, 400, response.get_data(as_text=True))
        self.assertFalse(response.get_json()['ok'])
        fetch.assert_not_called()

    def assert_invalid_placement(self, payload):
        with patch('app.routes.nba_live.persist_new_bets') as persist:
            response = self.client.post('/nba/place-bets', json=payload)
        self.assertEqual(response.status_code, 400, response.get_data(as_text=True))
        self.assertFalse(response.get_json()['success'])
        persist.assert_not_called()
        with self.app.app_context():
            self.assertEqual(Bet.query.count(), 0)

    def test_batch_rejects_non_object_items_before_any_fetch(self):
        for value in (None, 1, True, 'card', [], ['nested']):
            with self.subTest(value=value):
                self.assert_invalid_progress([self.progress(), value])

    def test_batch_rejects_bad_field_types(self):
        for field in ('player', 'prop_type', 'bet_type', 'espn_id', 'card_id', 'line'):
            for value in ([], {}, True):
                with self.subTest(field=field, value=value):
                    self.assert_invalid_progress([self.progress(**{field: value})])

    def test_batch_rejects_invalid_or_missing_identifiers(self):
        for value in ('', '../summary', 'game?x=1', 'x' * 65, 'game\n', 1.5):
            with self.subTest(value=value):
                self.assert_invalid_progress([self.progress(espn_id=value)])
        self.assert_invalid_progress([self.progress(card_id='')])
        self.assert_invalid_progress([self.progress(card_id='x' * 513)])

    def test_batch_rejects_nonfinite_lines(self):
        for value in (float('nan'), float('inf'), -float('inf'), 'NaN', 'Infinity', '1e999'):
            with self.subTest(value=value):
                self.assert_invalid_progress([self.progress(line=value)])

    def test_batch_rejects_duplicate_card_ids(self):
        self.assert_invalid_progress([self.progress(), self.progress(espn_id='game456')])

    def test_batch_caps_items_and_unique_games_before_fetch(self):
        self.assert_invalid_progress([self.progress(card_id=str(i)) for i in range(101)])
        self.assert_invalid_progress([
            self.progress(card_id=str(i), espn_id=str(i)) for i in range(16)
        ])

    def test_batch_accepts_limit_and_fetches_each_game_once(self):
        items = [self.progress(card_id=str(i), espn_id=str(i % 15)) for i in range(100)]
        with patch('app.routes.nba_live.fetch_summary_payload', return_value={}) as fetch:
            response = self.client.post('/nba/prop-progress/batch', json=items)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(set(response.get_json()), {str(i) for i in range(100)})
        self.assertEqual(fetch.call_count, 15)

    def test_batch_preserves_numeric_ids_and_optional_defaults(self):
        item = self.progress(card_id=12, espn_id=123, line=None, bet_type='')
        with patch('app.routes.nba_live.fetch_summary_payload', return_value={}) as fetch:
            response = self.client.post('/nba/prop-progress/batch', json=[item])
        self.assertEqual(response.status_code, 200)
        self.assertIn('12', response.get_json())
        fetch.assert_called_once_with('123', timeout=3, attempts=1)

    def test_single_progress_rejects_nonfinite_lines_and_long_ids(self):
        for espn_id, line in (('game123', 'NaN'), ('game123', 'Infinity'), ('x' * 65, '25.5')):
            with self.subTest(espn_id=espn_id, line=line):
                with patch('app.routes.nba_live.fetch_summary_payload') as fetch:
                    response = self.client.get(f'/nba/prop-progress/{espn_id}', query_string={
                        'player': 'LeBron James', 'prop_type': 'player_points', 'line': line,
                    })
                self.assertEqual(response.status_code, 400)
                fetch.assert_not_called()

    def test_placement_rejects_non_object_json(self):
        for payload in ([1], 'payload', 10, True):
            with self.subTest(payload=payload):
                self.assert_invalid_placement(payload)

    def test_placement_rejects_non_list_or_oversized_legs(self):
        for legs in (10, 'leg', {'team_a': 'A'}, [self.placement()['legs'][0]] * 101):
            with self.subTest(legs_type=type(legs).__name__):
                self.assert_invalid_placement({'stake': 25, 'legs': legs})

    def test_placement_rejects_nonfinite_top_level_numbers(self):
        for field in ('stake', 'units', 'bonus_multiplier'):
            for value in (float('nan'), float('inf'), -float('inf'), 'NaN', 'Infinity', '1e999', True, []):
                with self.subTest(field=field, value=value):
                    payload = self.placement()
                    payload[field] = value
                    self.assert_invalid_placement(payload)

    def test_placement_rejects_nonfinite_leg_numbers_atomically(self):
        for field in ('prop_line', 'over_under_line', 'american_odds'):
            for value in (float('nan'), float('inf'), -float('inf'), 'NaN', 'Infinity', True, []):
                with self.subTest(field=field, value=value):
                    payload = self.placement()
                    bad_leg = deepcopy(payload['legs'][0])
                    bad_leg[field] = value
                    payload['legs'].append(bad_leg)
                    self.assert_invalid_placement(payload)

    def test_placement_rejects_malformed_leg_fields(self):
        for field in ('team_a', 'team_b', 'game_id', 'player_name', 'prop_type', 'picked_team', 'bet_type'):
            for value in ([], {}, True):
                with self.subTest(field=field, value=value):
                    payload = self.placement()
                    payload['legs'][0][field] = value
                    self.assert_invalid_placement(payload)

    def test_placement_rejects_invalid_parlay_options(self):
        for field, value in (('is_parlay', 'false'), ('round_robin', []), ('round_robin', {'size': float('inf')}), ('round_robin', {'size': 2.5})):
            with self.subTest(field=field, value=value):
                payload = self.placement()
                payload[field] = value
                self.assert_invalid_placement(payload)

    def test_valid_legacy_placement_persists_server_owned_fields(self):
        payload = self.placement()
        payload.update(stake='25', units='1.5', bonus_multiplier='1.2', user_id=999, outcome='win')
        payload['legs'][0]['prop_line'] = '25.5'
        payload['legs'][0]['match_date'] = 'invalid-legacy-date'
        with patch('app.services.bet_placement_service.create_pick_context_for_bet'):
            response = self.client.post('/nba/place-bets', json=payload)
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        self.assertEqual(response.get_json()['count'], 1)
        with self.app.app_context():
            bet = Bet.query.one()
            self.assertEqual((bet.bet_amount, bet.units, bet.bonus_multiplier), (25, 1.5, 1.2))
            self.assertEqual(bet.prop_line, 25.5)
            self.assertEqual(bet.outcome, 'pending')
            self.assertNotEqual(bet.user_id, 999)

    def test_batch_deadline_defers_remaining_games_after_four_slow_reads(self):
        clock = [100.0]

        def slow_summary(_game, **kwargs):
            clock[0] += 3
            raise EspnClientError('fixture timeout')

        items = [self.progress(card_id=str(i), espn_id=str(i)) for i in range(15)]
        with patch('app.routes.nba_live.time.monotonic', side_effect=lambda: clock[0]), \
                patch('app.routes.nba_live.fetch_summary_payload', side_effect=slow_summary) as fetch:
            response = self.client.post('/nba/prop-progress/batch', json=items)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(clock[0], 112)
        self.assertEqual(fetch.call_count, 4)
        self.assertTrue(all(call.kwargs == {'timeout': 3, 'attempts': 1}
                            for call in fetch.call_args_list))
        results = response.get_json()
        self.assertEqual(len(results), 15)
        self.assertTrue(all(result['retryable'] for result in results.values()))
        self.assertEqual(results['14']['status'], 'progress_deferred')

    def test_single_summary_failure_and_empty_boxscore_are_retryable(self):
        for result in (EspnClientError('fixture'), {'boxscore': {'players': []}}):
            with self.subTest(result=result), patch('app.routes.nba_live.fetch_summary_payload') as fetch:
                if isinstance(result, Exception):
                    fetch.side_effect = result
                else:
                    fetch.return_value = result
                response = self.client.get('/nba/prop-progress/game123', query_string={
                    'player': 'LeBron James', 'prop_type': 'player_points',
                })
                self.assertEqual(response.status_code, 200)
                self.assertTrue(response.get_json()['retryable'])
                fetch.assert_called_once_with('game123', timeout=3, attempts=1)

    def test_progress_timeout_does_not_retry_transport(self):
        with patch('app.services.espn_client.requests.get', side_effect=requests.Timeout('fixture')) as fetch, \
                patch('app.services.provider_access.time.sleep') as sleep:
            response = self.client.post('/nba/prop-progress/batch', json=[self.progress()])
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.get_json()['card-1']['retryable'])
        self.assertEqual(fetch.call_count, 1)
        self.assertEqual(fetch.call_args.kwargs['timeout'], 3)
        sleep.assert_not_called()

    def test_batch_last_fetch_uses_only_remaining_budget(self):
        clock = [100.0]

        def summary(_game, **kwargs):
            clock[0] += 10.5 if _game == '0' else kwargs['timeout']
            return {}

        items = [self.progress(card_id=str(i), espn_id=str(i)) for i in range(3)]
        with patch('app.routes.nba_live.time.monotonic', side_effect=lambda: clock[0]), \
                patch('app.routes.nba_live.fetch_summary_payload', side_effect=summary) as fetch:
            response = self.client.post('/nba/prop-progress/batch', json=items)
        self.assertEqual([call.kwargs['timeout'] for call in fetch.call_args_list], [3, 1.5])
        self.assertEqual(response.get_json()['2']['status'], 'progress_deferred')


class TestSummaryCacheBudget(unittest.TestCase):
    def test_summary_cache_prunes_expired_and_stays_within_512_entries(self):
        from app.routes import nba_live
        nba_live._GAME_SUMMARY_CACHE.clear()
        self.addCleanup(nba_live._GAME_SUMMARY_CACHE.clear)
        nba_live._GAME_SUMMARY_CACHE['expired'] = {'data': {}, 'expires_at': 99}
        nba_live._GAME_SUMMARY_CACHE.update({
            str(i): {'data': {'fixture': i}, 'expires_at': 200 + i}
            for i in range(512)
        })
        with patch('app.routes.nba_live.fetch_summary_payload', return_value={'fixture': 'new'}) as fetch:
            nba_live._get_game_summary('new', 100)
        self.assertNotIn('expired', nba_live._GAME_SUMMARY_CACHE)
        self.assertEqual(len(nba_live._GAME_SUMMARY_CACHE), 512)
        self.assertIn('new', nba_live._GAME_SUMMARY_CACHE)
        fetch.assert_called_once_with('new', timeout=3, attempts=1)
