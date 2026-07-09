"""Verify Odds API calls in nba_service route through the budget manager."""

from unittest.mock import MagicMock, patch

from tests.helpers import BaseTestCase


def _fake_response(json_payload):
    resp = MagicMock()
    resp.json.return_value = json_payload
    resp.headers = {'x-requests-remaining': '400'}
    resp.raise_for_status.return_value = None
    return resp


class TestBudgetWiring(BaseTestCase):

    def setUp(self):
        super().setUp()
        from app.services.api_budget import ODDS_BUDGET
        ODDS_BUDGET._remaining = None

    def tearDown(self):
        from app.services.api_budget import ODDS_BUDGET
        ODDS_BUDGET._remaining = None
        super().tearDown()

    @patch.dict('os.environ', {'ODDS_API_KEY': 'test-key'})
    @patch('app.services.api_budget.requests.get')
    def test_fetch_odds_combined_uses_budgeted_get(self, mock_get):
        mock_get.return_value = _fake_response([])
        from app.services import nba_service
        from app.services.api_budget import ODDS_BUDGET
        totals, h2h = nba_service.fetch_odds_combined()
        self.assertEqual((totals, h2h), ({}, {}))
        mock_get.assert_called_once()          # went through api_budget module
        self.assertEqual(ODDS_BUDGET.remaining, 400.0)

    @patch.dict('os.environ', {'ODDS_API_KEY': 'test-key'})
    @patch('app.services.api_budget.requests.get')
    def test_fetch_odds_events_uses_budgeted_get(self, mock_get):
        mock_get.return_value = _fake_response([])
        from app.services import nba_service
        from app.services.api_budget import ODDS_BUDGET
        result = nba_service.fetch_odds_events()
        self.assertEqual(result, {})
        mock_get.assert_called_once()
        self.assertEqual(ODDS_BUDGET.remaining, 400.0)

    @patch.dict('os.environ', {'ODDS_API_KEY': 'test-key'})
    def test_budget_exhaustion_degrades_to_empty(self):
        from app.services import nba_service
        from app.services.api_budget import ODDS_BUDGET
        ODDS_BUDGET.record_headers({'x-requests-remaining': '1'})
        try:
            totals, h2h = nba_service.fetch_odds_combined()
            self.assertEqual((totals, h2h), ({}, {}))
        finally:
            ODDS_BUDGET._remaining = None   # reset singleton for other tests
