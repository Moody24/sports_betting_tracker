"""Source status must never confuse credentials or a page refresh with live data."""

import unittest
from unittest.mock import patch

from flask import Flask


class TestDataStatus(unittest.TestCase):
    def status(self, enabled=True, state='unavailable', last_success=None):
        from app.services.data_status import data_status_context
        with patch('app.services.data_status.provider_access_status', return_value={
            'espn': {'enabled': enabled}, 'odds_api': {'enabled': enabled},
        }), patch('app.services.data_status.get_scoreboard_status', return_value={
            'state': state, 'last_success_at': last_success,
        }):
            return data_status_context()

    def test_credentials_do_not_claim_live_data(self):
        result = self.status()
        self.assertEqual(result['scoreboard']['state'], 'unavailable')
        self.assertEqual(result['odds']['state'], 'configured')
        self.assertNotIn('live', result['odds']['age_label'])

    def test_source_approval_overrides_cached_success(self):
        result = self.status(enabled=False, state='fresh')
        self.assertEqual(result['scoreboard']['state'], 'unavailable')
        self.assertEqual(result['odds']['state'], 'unavailable')

    def test_stale_data_retains_real_success_time_in_et(self):
        result = self.status(state='stale', last_success='2026-10-21T01:30:00+00:00')
        self.assertEqual(result['scoreboard']['state'], 'stale')
        self.assertEqual(result['scoreboard']['last_success_display'], 'Oct 20, 09:30 PM ET')

    def test_missing_or_naive_time_cannot_look_recent(self):
        for value in (None, 'bad', '2026-10-20T12:00:00'):
            with self.subTest(value=value):
                self.assertIsNone(self.status(last_success=value)['scoreboard']['last_success_display'])

    def test_shared_feed_status_uses_actual_fetch_without_provider_calls(self):
        app = Flask('source-status')
        app.config['BALLDONTLIE_API_KEY'] = 'test-only-key'
        with app.app_context(), patch('app.services.basketball_feed.get_feed_snapshot', return_value={
            'state': 'current', 'last_success_at': '2026-10-21T01:30:00+00:00',
        }):
            result = self.status(enabled=False)
        self.assertEqual(result['scoreboard']['state'], 'fresh')
        self.assertIn('provider delay not guaranteed', result['scoreboard']['age_label'])
        self.assertEqual(result['scoreboard']['last_success_display'], 'Oct 20, 09:30 PM ET')

    def test_shared_feed_database_failure_cannot_claim_current(self):
        app = Flask('source-status')
        app.config['BALLDONTLIE_API_KEY'] = 'test-only-key'
        with app.app_context(), patch('app.services.basketball_feed.get_feed_snapshot', side_effect=RuntimeError):
            result = self.status(state='fresh')
        self.assertEqual(result['scoreboard']['state'], 'unavailable')
        self.assertIsNone(result['scoreboard']['last_success_display'])
