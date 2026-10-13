"""Source admission and bounded transport behavior, using HTTP fixtures only."""

import json
import os
import sys
import unittest
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import Mock, patch

from flask import Flask
import requests

from app.services.espn_client import EspnClientError, fetch_json
from app.services.api_budget import APIBudgetManager, BudgetExhaustedError
from app.services.provider_access import provider_access_status


def response(status=200, payload=None, headers=None):
    result = requests.Response()
    result.status_code = status
    result._content = json.dumps(payload if payload is not None else {}).encode()
    result._content_consumed = True
    result.headers.update(headers or {})
    result.url = 'https://example.test/data?apiKey=fixture-secret'
    return result


class ProviderAccessTests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, {}, clear=True)
        self.env.start()
        self.addCleanup(self.env.stop)
        self.http = patch('app.services.espn_client.requests.get')
        self.get = self.http.start()
        self.addCleanup(self.http.stop)
        self.get.return_value = response(payload={'events': []})

    def test_espn_is_blocked_without_explicit_permission(self):
        with self.assertRaisesRegex(EspnClientError, 'permission'):
            fetch_json('https://example.test')
        self.get.assert_not_called()

    def test_approval_requires_rights_basis(self):
        os.environ['ESPN_ACCESS_APPROVED'] = 'true'
        with self.assertRaisesRegex(EspnClientError, 'permission'):
            fetch_json('https://example.test')
        self.get.assert_not_called()

    def test_explicit_approval_with_basis_admits_request(self):
        os.environ.update(ESPN_ACCESS_APPROVED='true', ESPN_RIGHTS_BASIS='fixture-license')
        self.assertEqual(fetch_json('https://example.test'), {'events': []})

    def test_flask_testing_context_admits_fixture_requests(self):
        app = Flask(__name__)
        app.config['TESTING'] = True
        with app.app_context():
            self.assertEqual(fetch_json('https://example.test'), {'events': []})

    def test_non_testing_context_and_unittest_argv_do_not_admit_requests(self):
        app = Flask(__name__)
        with app.app_context(), patch('sys.argv', ['python', '-m', 'unittest']):
            with self.assertRaises(EspnClientError):
                fetch_json('https://example.test')
        self.get.assert_not_called()

    def test_status_and_flask_config_override_do_not_expose_secrets(self):
        os.environ.update(ESPN_ACCESS_APPROVED='true', ESPN_RIGHTS_BASIS='private-license',
                          ODDS_API_KEY='fixture-secret')
        app = Flask(__name__)
        app.config['ESPN_ACCESS_APPROVED'] = False
        with app.app_context():
            status = provider_access_status()
        self.assertFalse(status['espn']['enabled'])
        self.assertEqual(status['espn']['state'], 'blocked')
        self.assertTrue(status['espn']['rights_basis_configured'])
        self.assertEqual(status['odds_api']['state'], 'configured')
        self.assertNotIn('private-license', json.dumps(status))
        self.assertNotIn('fixture-secret', json.dumps(status))

    def test_remote_espn_derived_releases_require_same_permission(self):
        from app.services.hoopr_import_service import _load_player_box_df
        from app.services.release_data_service import load_release
        for loader in (_load_player_box_df, lambda year: load_release('team', year)):
            with self.subTest(loader=loader), patch('pandas.read_parquet'):
                with self.assertRaisesRegex(requests.RequestException, 'permission'):
                    loader(2027)
        self.get.assert_not_called()

    def test_approved_release_downloads_remain_available(self):
        import pandas as pd
        from app.services.hoopr_import_service import _load_player_box_df
        from app.services.release_data_service import load_release
        os.environ.update(ESPN_ACCESS_APPROVED='true', ESPN_RIGHTS_BASIS='fixture-license')
        with patch('pandas.read_parquet', return_value=pd.DataFrame({'points': [10]})):
            player_frame = _load_player_box_df(2027)
            team_frame, _ = load_release('team', 2027)
        self.assertEqual(player_frame['points'].tolist(), [10])
        self.assertEqual(team_frame['points'].tolist(), [10])

    def test_local_release_imports_do_not_require_remote_permission(self):
        import pandas as pd
        from app.services.hoopr_import_service import _load_player_box_df
        from app.services.release_data_service import load_release
        with patch('pandas.read_parquet', return_value=pd.DataFrame({'points': [10]})):
            player_frame = _load_player_box_df(2027, from_dir='/fixtures')
            team_frame, version = load_release('team', 2027, from_dir='/fixtures')
        self.assertEqual(player_frame['points'].tolist(), [10])
        self.assertEqual(team_frame['points'].tolist(), [10])
        self.assertTrue(version.startswith('local:'))
        self.get.assert_not_called()

    def test_nba_network_endpoints_are_blocked_without_nba_permission(self):
        from app.cli.history_commands import _fetch_advanced_boxscore_df, _fetch_league_log_df
        from app.services.matchup_service import fetch_team_defense_stats
        from app.services.stats_service import fetch_player_game_logs
        os.environ.update(ESPN_ACCESS_APPROVED='true', ESPN_RIGHTS_BASIS='espn-only')
        with self.nba_endpoints() as endpoints:
            self.assertEqual(fetch_player_game_logs('123'), [])
            self.assertEqual(fetch_team_defense_stats(), [])
            with self.assertRaisesRegex(requests.RequestException, 'permission'):
                fetch_player_game_logs('123', raise_on_error=True)
            with self.assertRaisesRegex(requests.RequestException, 'permission'):
                _fetch_league_log_df('2026-27', 'Regular Season')
            with self.assertRaisesRegex(requests.RequestException, 'permission'):
                _fetch_advanced_boxscore_df('123')
            for endpoint in endpoints.values():
                next(iter(vars(endpoint).values())).assert_not_called()

    @staticmethod
    @contextmanager
    def nba_endpoints():
        import pandas as pd

        def constructor():
            return Mock(return_value=SimpleNamespace(
                get_data_frames=lambda: [pd.DataFrame()]))

        endpoints = {
            'playergamelog': SimpleNamespace(PlayerGameLog=constructor()),
            'leaguedashteamstats': SimpleNamespace(LeagueDashTeamStats=constructor()),
            'leaguegamelog': SimpleNamespace(LeagueGameLog=constructor()),
            'boxscoreadvancedv2': SimpleNamespace(BoxScoreAdvancedV2=constructor()),
        }
        with patch.dict(sys.modules, {
            'nba_api.stats.endpoints': SimpleNamespace(**endpoints),
        }):
            yield endpoints

    def test_explicit_nba_approval_reaches_only_mocked_endpoints(self):
        from app.cli.history_commands import _fetch_advanced_boxscore_df, _fetch_league_log_df
        from app.services.matchup_service import fetch_team_defense_stats
        from app.services.stats_service import fetch_player_game_logs
        os.environ.update(NBA_ACCESS_APPROVED='true', NBA_RIGHTS_BASIS='nba-fixture-license')
        with self.nba_endpoints() as endpoints, patch('time.sleep'):
            self.assertEqual(fetch_player_game_logs('123'), [])
            self.assertEqual(fetch_team_defense_stats(), [])
            self.assertTrue(_fetch_league_log_df('2026-27', 'Regular Season').empty)
            self.assertTrue(_fetch_advanced_boxscore_df('123').empty)
            for endpoint in endpoints.values():
                next(iter(vars(endpoint).values())).assert_called_once()
        state = provider_access_status()['nba_stats']
        self.assertEqual(state['state'], 'approved')
        self.assertTrue(state['rights_basis_configured'])
        self.assertNotIn('nba-fixture-license', json.dumps(state))
        self.assertFalse(provider_access_status()['espn']['enabled'])

    def test_nba_approval_alone_is_not_sufficient(self):
        os.environ['NBA_ACCESS_APPROVED'] = 'true'
        self.assertFalse(provider_access_status()['nba_stats']['enabled'])

    def test_nba_config_override_and_unittest_argv_do_not_bypass_denial(self):
        from app.services.provider_access import require_nba_stats_access
        os.environ.update(NBA_ACCESS_APPROVED='true', NBA_RIGHTS_BASIS='private-nba-license')
        app = Flask(__name__)
        app.config['NBA_ACCESS_APPROVED'] = False
        with app.app_context(), patch('sys.argv', ['python', '-m', 'unittest']):
            with self.assertRaisesRegex(requests.RequestException, 'permission'):
                require_nba_stats_access()
            status = provider_access_status()['nba_stats']
            self.assertEqual(status['state'], 'blocked')
            self.assertNotIn('private-nba-license', json.dumps(status))

    def test_nba_testing_context_admits_only_fixture_endpoint(self):
        from app.cli.history_commands import _fetch_advanced_boxscore_df
        app = Flask(__name__)
        app.config['TESTING'] = True
        with app.app_context(), self.nba_endpoints() as endpoints:
            self.assertTrue(_fetch_advanced_boxscore_df('123').empty)
            self.assertEqual(provider_access_status()['nba_stats']['state'], 'testing')
            endpoints['boxscoreadvancedv2'].BoxScoreAdvancedV2.assert_called_once()


class ProviderRetryTests(unittest.TestCase):
    def setUp(self):
        app = Flask(__name__)
        app.config['TESTING'] = True
        context = app.app_context()
        context.push()
        self.addCleanup(context.pop)
        self.sleep_patch = patch('time.sleep')
        self.sleep = self.sleep_patch.start()
        self.addCleanup(self.sleep_patch.stop)
        self.get_patch = patch('requests.get')
        self.get = self.get_patch.start()
        self.addCleanup(self.get_patch.stop)

    def test_espn_auth_failure_is_not_retried_or_leaked(self):
        self.get.return_value = response(401)
        with self.assertRaises(EspnClientError) as error:
            fetch_json('https://example.test/?apiKey=fixture-secret', attempts=3)
        self.assertNotIn('fixture-secret', str(error.exception))
        self.assertNotIn('https://', str(error.exception))
        self.assertEqual(self.get.call_count, 1)
        self.sleep.assert_not_called()

    def test_espn_retries_transient_response_after_retry_after(self):
        self.get.side_effect = [response(503, headers={'Retry-After': '2'}),
                                response(payload={'events': []})]
        self.assertEqual(fetch_json('https://example.test', attempts=3), {'events': []})
        self.sleep.assert_called_once_with(2.0)

    def test_espn_timeout_retries_have_hard_attempt_and_timeout_caps(self):
        self.get.side_effect = requests.Timeout('url?apiKey=fixture-secret')
        with self.assertRaises(EspnClientError) as error:
            fetch_json('https://example.test', attempts=99, timeout=100)
        self.assertNotIn('fixture-secret', str(error.exception))
        self.assertEqual(self.get.call_count, 3)
        self.assertTrue(all(call.kwargs['timeout'] <= 15 for call in self.get.call_args_list))
        self.assertEqual(self.sleep.call_count, 2)
        self.assertTrue(all(0 < call.args[0] <= 10 for call in self.sleep.call_args_list))

    def test_invalid_json_shape_is_not_retried(self):
        self.get.return_value = response(payload=[])
        with self.assertRaises(EspnClientError):
            fetch_json('https://example.test', attempts=3)
        self.assertEqual(self.get.call_count, 1)

    def test_long_retry_after_stops_without_retrying_early(self):
        self.get.return_value = response(429, headers={'Retry-After': '120'})
        with self.assertRaises(EspnClientError):
            fetch_json('https://example.test', attempts=3)
        self.assertEqual(self.get.call_count, 1)
        self.sleep.assert_not_called()

    def test_odds_retries_429_and_records_final_quota(self):
        self.get.side_effect = [response(429, headers={'Retry-After': '1'}),
                                response(headers={'x-requests-remaining': '90'})]
        manager = APIBudgetManager()
        result = manager.budgeted_get('https://example.test', params={'apiKey': 'fixture-secret'})
        self.assertEqual(result.status_code, 200)
        self.assertEqual(manager.remaining, 90)
        self.sleep.assert_called_once_with(1.0)

    def test_odds_auth_failure_raises_safe_error_without_retry(self):
        self.get.return_value = response(403)
        with self.assertRaises(requests.RequestException) as error:
            APIBudgetManager().budgeted_get('https://example.test?apiKey=fixture-secret')
        self.assertNotIn('fixture-secret', str(error.exception))
        self.assertNotIn('https://', str(error.exception))
        self.assertEqual(self.get.call_count, 1)

    def test_odds_timeout_exhaustion_redacts_underlying_exception(self):
        self.get.side_effect = requests.Timeout('url?apiKey=fixture-secret')
        with self.assertRaises(requests.RequestException) as error:
            APIBudgetManager().budgeted_get('https://example.test')
        self.assertNotIn('fixture-secret', str(error.exception))
        self.assertEqual(self.get.call_count, 3)

    def test_odds_rechecks_quota_before_retrying(self):
        self.get.return_value = response(503, headers={'x-requests-remaining': '2'})
        manager = APIBudgetManager(floor=25)
        with self.assertRaises(BudgetExhaustedError):
            manager.budgeted_get('https://example.test')
        self.assertEqual(manager.remaining, 2)
        self.assertEqual(self.get.call_count, 1)


if __name__ == '__main__':
    unittest.main()
