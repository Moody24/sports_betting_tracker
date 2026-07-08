"""Tests for the Odds API request budget manager."""

from unittest.mock import MagicMock, patch

from tests.helpers import BaseTestCase


class TestAPIBudgetManager(BaseTestCase):

    def _manager(self, floor=25):
        from app.services.api_budget import APIBudgetManager
        return APIBudgetManager(floor=floor)

    def test_unknown_budget_allows_spending(self):
        mgr = self._manager()
        self.assertIsNone(mgr.remaining)
        self.assertTrue(mgr.can_spend())
        self.assertTrue(mgr.can_spend(critical=True))

    def test_records_quota_headers_case_insensitive(self):
        mgr = self._manager()
        mgr.record_headers({'X-Requests-Remaining': '123.0', 'X-Requests-Used': '377'})
        self.assertEqual(mgr.remaining, 123.0)

    def test_blocks_non_critical_below_floor_allows_critical(self):
        mgr = self._manager(floor=50)
        mgr.record_headers({'x-requests-remaining': '10'})
        self.assertFalse(mgr.can_spend())
        self.assertTrue(mgr.can_spend(critical=True))

    def test_malformed_headers_ignored(self):
        mgr = self._manager()
        mgr.record_headers({'x-requests-remaining': 'garbage'})
        self.assertIsNone(mgr.remaining)
        self.assertTrue(mgr.can_spend())

    @patch('app.services.api_budget.requests.get')
    def test_budgeted_get_records_headers(self, mock_get):
        resp = MagicMock()
        resp.headers = {'x-requests-remaining': '99'}
        mock_get.return_value = resp
        mgr = self._manager()
        out = mgr.budgeted_get('https://example.com', params={'a': 1}, timeout=5)
        self.assertIs(out, resp)
        self.assertEqual(mgr.remaining, 99.0)
        mock_get.assert_called_once_with(
            'https://example.com', params={'a': 1}, timeout=5
        )

    @patch('app.services.api_budget.requests.get')
    def test_budgeted_get_raises_when_exhausted(self, mock_get):
        from app.services.api_budget import BudgetExhaustedError
        mgr = self._manager(floor=50)
        mgr.record_headers({'x-requests-remaining': '5'})
        with self.assertRaises(BudgetExhaustedError):
            mgr.budgeted_get('https://example.com')
        mock_get.assert_not_called()

    def test_exhausted_error_is_request_exception(self):
        import requests
        from app.services.api_budget import BudgetExhaustedError
        self.assertTrue(issubclass(BudgetExhaustedError, requests.RequestException))
