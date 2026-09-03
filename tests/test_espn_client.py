"""Unit tests for the centralized ESPN transport adapter."""

import unittest
from unittest.mock import MagicMock, patch

import requests

from app.services.espn_client import (
    ESPN_SUMMARY_URL,
    EspnClientError,
    fetch_json,
    fetch_summary_payload,
)


class EspnClientTests(unittest.TestCase):
    @patch('app.services.espn_client.requests.get')
    def test_summary_builds_request_in_one_place(self, mock_get):
        response = MagicMock()
        response.json.return_value = {'boxscore': {}}
        mock_get.return_value = response

        self.assertEqual(fetch_summary_payload('game-1', timeout=8), {'boxscore': {}})
        mock_get.assert_called_once_with(
            ESPN_SUMMARY_URL,
            params={'event': 'game-1'},
            timeout=8,
            headers={'User-Agent': 'sports-betting-tracker/1.0'},
        )

    @patch('app.services.espn_client.requests.get')
    def test_fetch_json_retries_transient_failures(self, mock_get):
        response = MagicMock()
        response.json.return_value = {'events': []}
        mock_get.side_effect = [requests.RequestException('timeout'), response]

        self.assertEqual(fetch_json('https://example.test', attempts=2), {'events': []})
        self.assertEqual(mock_get.call_count, 2)

    @patch('app.services.espn_client.requests.get')
    def test_fetch_json_wraps_invalid_payload(self, mock_get):
        response = MagicMock()
        response.json.return_value = []
        mock_get.return_value = response

        with self.assertRaises(EspnClientError):
            fetch_json('https://example.test')


if __name__ == '__main__':
    unittest.main()
