"""Focused tests for NBA service odds parsing."""

import unittest
from unittest.mock import patch


class TestNBAService(unittest.TestCase):
    @patch.dict("os.environ", {"ODDS_API_KEY": "test-key"})
    def test_fetch_odds_combined_returns_spreads_map(self):
        canned = [{
            "home_team": "Denver Nuggets",
            "away_team": "Los Angeles Lakers",
            "bookmakers": [{
                "key": "fanduel",
                "markets": [
                    {"key": "totals", "outcomes": [
                        {"name": "Over", "point": 228.5},
                        {"name": "Under", "point": 228.5},
                    ]},
                    {"key": "h2h", "outcomes": [
                        {"name": "Denver Nuggets", "price": -320},
                        {"name": "Los Angeles Lakers", "price": 260},
                    ]},
                    {"key": "spreads", "outcomes": [
                        {"name": "Denver Nuggets", "point": -8.5},
                        {"name": "Los Angeles Lakers", "point": 8.5},
                    ]},
                ],
            }],
        }]
        with patch("app.services.nba_service.ODDS_BUDGET.budgeted_get") as get:
            get.return_value.json.return_value = canned
            get.return_value.raise_for_status.return_value = None
            from app.services.nba_service import _matchup_key, fetch_odds_combined
            totals, h2h, spreads = fetch_odds_combined()

        key = _matchup_key("Denver Nuggets", "Los Angeles Lakers")
        self.assertEqual(spreads[key], {"spread": 8.5, "favored": "home"})
