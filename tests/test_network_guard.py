"""Fixture tests must not accidentally reach provider session transports."""

from unittest.mock import Mock, patch

import requests

from tests.helpers import BaseTestCase


class TestFixtureNetworkGuard(BaseTestCase):
    def test_direct_requests_session_is_blocked_before_adapter_send(self):
        with patch.object(requests.adapters.HTTPAdapter, 'send') as send:
            with self.assertRaisesRegex(requests.RequestException, 'External HTTP is disabled'):
                requests.Session().get('https://fixture.invalid', timeout=0.1)
        send.assert_not_called()
        self._session_http_mock.assert_called_once()

    def test_real_nba_endpoint_constructors_are_blocked_before_adapter_send(self):
        from app.services.matchup_service import fetch_team_defense_stats
        from app.services.stats_service import fetch_player_game_logs

        with self.app.app_context(), patch.object(requests.adapters.HTTPAdapter, 'send') as send:
            self.assertEqual(fetch_player_game_logs('fixture', season='2026-27'), [])
            self.assertEqual(fetch_team_defense_stats(), [])
        send.assert_not_called()
        self.assertEqual(self._session_http_mock.call_count, 2)

    def test_explicit_requests_get_fixture_remains_usable(self):
        response = Mock(status_code=200)
        with patch('requests.get', return_value=response) as get:
            self.assertIs(requests.get('https://fixture.invalid'), response)
        get.assert_called_once()
        self._session_http_mock.assert_not_called()
