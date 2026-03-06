"""Tests for the NBA service layer."""

import json
import os
import unittest
from datetime import datetime
from unittest.mock import MagicMock, patch

from app.services import nba_service

from tests.helpers import BaseTestCase, make_bet

# ── Mock data ────────────────────────────────────────────────────────

MOCK_ESPN_RESPONSE = {
    "events": [{
        "id": "espn123",
        "name": "Lakers vs Celtics",
        "date": "2025-03-01T00:00:00Z",
        "status": {
            "displayClock": "0:00",
            "period": 4,
            "type": {
                "name": "STATUS_FINAL",
                "detail": "Final",
                "description": "Final",
            },
        },
        "competitions": [{
            "competitors": [
                {
                    "homeAway": "home",
                    "score": "110",
                    "team": {"displayName": "Los Angeles Lakers", "abbreviation": "LAL", "logo": ""},
                },
                {
                    "homeAway": "away",
                    "score": "105",
                    "team": {"displayName": "Boston Celtics", "abbreviation": "BOS", "logo": ""},
                },
            ]
        }],
    }]
}


class TestNBAService(unittest.TestCase):
    """Unit tests for nba_service with mocked HTTP calls."""

    def setUp(self):
        # Clear module-level caches so each test starts from a clean slate.
        nba_service._GAMES_CACHE.clear()
        nba_service._UPCOMING_CACHE.clear()

    # fetch_espn_scoreboard
    @patch("app.services.nba_service.requests.get")
    def test_fetch_espn_scoreboard_success(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.json.return_value = MOCK_ESPN_RESPONSE
        mock_resp.raise_for_status.return_value = None
        mock_get.return_value = mock_resp

        games = nba_service.fetch_espn_scoreboard()
        self.assertEqual(len(games), 1)
        self.assertEqual(games[0]["espn_id"], "espn123")
        self.assertEqual(games[0]["total_score"], 215)
        self.assertEqual(games[0]["status"], "STATUS_FINAL")

    @patch("app.services.nba_service.requests.get")
    def test_fetch_espn_scoreboard_network_error(self, mock_get):
        mock_get.side_effect = nba_service.requests.RequestException("timeout")
        self.assertEqual(nba_service.fetch_espn_scoreboard(), [])

    @patch("app.services.nba_service.requests.get")
    def test_fetch_espn_scoreboard_malformed_json(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.raise_for_status.return_value = None
        mock_resp.json.side_effect = ValueError("bad json")
        mock_get.return_value = mock_resp
        self.assertEqual(nba_service.fetch_espn_scoreboard(), [])

    @patch("app.services.nba_service.requests.get")
    def test_fetch_espn_scoreboard_skips_incomplete_events(self, mock_get):
        """Events missing home or away competitor are skipped."""
        mock_resp = MagicMock()
        mock_resp.raise_for_status.return_value = None
        mock_resp.json.return_value = {
            "events": [{"id": "x", "name": "x", "date": "", "status": {"displayClock": "", "period": 0, "type": {}},
                        "competitions": [{"competitors": []}]}]
        }
        mock_get.return_value = mock_resp
        self.assertEqual(nba_service.fetch_espn_scoreboard(), [])

    # fetch_odds
    @patch.dict(os.environ, {}, clear=True)
    def test_fetch_odds_no_api_key_returns_empty(self):
        os.environ.pop("ODDS_API_KEY", None)
        self.assertEqual(nba_service.fetch_odds(), {})

    @patch.dict(os.environ, {"ODDS_API_KEY": "test-key"})
    @patch("app.services.nba_service.requests.get")
    def test_fetch_odds_success(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.raise_for_status.return_value = None
        mock_resp.json.return_value = [{
            "home_team": "Los Angeles Lakers",
            "away_team": "Boston Celtics",
            "bookmakers": [{
                "markets": [{
                    "key": "totals",
                    "outcomes": [
                        {"name": "Over", "point": 215.5},
                        {"name": "Under", "point": 215.5},
                    ],
                }]
            }],
        }]
        mock_get.return_value = mock_resp

        odds = nba_service.fetch_odds()
        self.assertEqual(len(odds), 1)
        key = nba_service._matchup_key("Los Angeles Lakers", "Boston Celtics")
        self.assertAlmostEqual(odds[key], 215.5)

    @patch.dict(os.environ, {"ODDS_API_KEY": "test-key"})
    @patch("app.services.nba_service.requests.get")
    def test_fetch_odds_network_error(self, mock_get):
        mock_get.side_effect = nba_service.requests.RequestException("timeout")
        self.assertEqual(nba_service.fetch_odds(), {})

    # fetch_odds_events
    @patch.dict(os.environ, {}, clear=True)
    def test_fetch_odds_events_no_api_key(self):
        os.environ.pop("ODDS_API_KEY", None)
        self.assertEqual(nba_service.fetch_odds_events(), {})

    @patch.dict(os.environ, {"ODDS_API_KEY": "test-key"})
    @patch("app.services.nba_service.requests.get")
    def test_fetch_odds_events_network_error(self, mock_get):
        mock_get.side_effect = nba_service.requests.RequestException("timeout")
        self.assertEqual(nba_service.fetch_odds_events(), {})

    # fetch_player_props_for_event
    @patch.dict(os.environ, {}, clear=True)
    def test_fetch_player_props_no_api_key(self):
        os.environ.pop("ODDS_API_KEY", None)
        self.assertEqual(nba_service.fetch_player_props_for_event("event123"), {})

    @patch.dict(os.environ, {"ODDS_API_KEY": "test-key"})
    def test_fetch_player_props_no_event_id(self):
        self.assertEqual(nba_service.fetch_player_props_for_event(""), {})

    @patch.dict(os.environ, {"ODDS_API_KEY": "test-key"})
    @patch("app.services.nba_service.requests.get")
    def test_fetch_player_props_network_error(self, mock_get):
        mock_get.side_effect = nba_service.requests.RequestException("timeout")
        self.assertEqual(nba_service.fetch_player_props_for_event("event123"), {})

    # _matchup_key
    def test_matchup_key_is_order_independent(self):
        key1 = nba_service._matchup_key("Los Angeles Lakers", "Boston Celtics")
        key2 = nba_service._matchup_key("Boston Celtics", "Los Angeles Lakers")
        self.assertEqual(key1, key2)

    def test_matchup_key_normalises_case_and_whitespace(self):
        key1 = nba_service._matchup_key("  Lakers  ", "Celtics")
        key2 = nba_service._matchup_key("lakers", "CELTICS")
        self.assertEqual(key1, key2)

    def test_matchup_key_normalises_common_team_aliases(self):
        key1 = nba_service._matchup_key("LA Clippers", "NY Knicks")
        key2 = nba_service._matchup_key("Los Angeles Clippers", "New York Knicks")
        self.assertEqual(key1, key2)

    # get_todays_games
    @patch("app.services.nba_service.fetch_espn_scoreboard")
    @patch("app.services.nba_service.fetch_odds_combined")
    @patch("app.services.nba_service.fetch_odds_events")
    def test_get_todays_games_merges_correctly(self, mock_events, mock_odds_combined, mock_espn):
        mock_espn.return_value = [{
            "espn_id": "espn123",
            "home": {"name": "Los Angeles Lakers"},
            "away": {"name": "Boston Celtics"},
            "total_score": 215,
            "status": "STATUS_FINAL",
        }]
        key = nba_service._matchup_key("Los Angeles Lakers", "Boston Celtics")
        mock_odds_combined.return_value = ({key: 215.5}, {})
        mock_events.return_value = {key: "odds_event_abc"}

        games = nba_service.get_todays_games()
        self.assertEqual(len(games), 1)
        self.assertAlmostEqual(games[0]["over_under_line"], 215.5)
        self.assertEqual(games[0]["odds_event_id"], "odds_event_abc")

    @patch("app.services.nba_service.fetch_espn_scoreboard")
    @patch("app.services.nba_service.fetch_odds_combined", return_value=({}, {}))
    @patch("app.services.nba_service.fetch_odds_events", return_value={})
    def test_get_todays_games_no_odds_match(self, _e, _o, mock_espn):
        mock_espn.return_value = [{
            "espn_id": "espn999",
            "home": {"name": "Team A"},
            "away": {"name": "Team B"},
            "total_score": 200,
            "status": "STATUS_SCHEDULED",
        }]
        games = nba_service.get_todays_games()
        self.assertIsNone(games[0]["over_under_line"])
        self.assertEqual(games[0]["odds_event_id"], "")

    # resolve_pending_bets
    def _game(self, status="STATUS_FINAL", total=215):
        return [{"espn_id": "espn123", "status": status, "total_score": total}]

    def _bet(self, bet_type="over", line=210.0):
        class FakeBet:
            external_game_id = "espn123"
        fb = FakeBet()
        fb.bet_type = bet_type
        fb.over_under_line = line
        fb.picked_team = None
        fb.is_player_prop = False
        fb.player_name = None
        fb.prop_type = None
        fb.prop_line = None
        return fb

    def test_resolve_over_win(self):
        with patch("app.services.nba_service.fetch_espn_scoreboard", return_value=self._game(total=215)):
            _, outcome, total = nba_service.resolve_pending_bets([self._bet("over", 210)])[0]
        self.assertEqual(outcome, "win")
        self.assertAlmostEqual(total, 215.0)

    def test_resolve_over_lose(self):
        with patch("app.services.nba_service.fetch_espn_scoreboard", return_value=self._game(total=205)):
            _, outcome, _ = nba_service.resolve_pending_bets([self._bet("over", 210)])[0]
        self.assertEqual(outcome, "lose")

    def test_resolve_under_win(self):
        with patch("app.services.nba_service.fetch_espn_scoreboard", return_value=self._game(total=205)):
            _, outcome, _ = nba_service.resolve_pending_bets([self._bet("under", 210)])[0]
        self.assertEqual(outcome, "win")

    def test_resolve_under_lose(self):
        with patch("app.services.nba_service.fetch_espn_scoreboard", return_value=self._game(total=215)):
            _, outcome, _ = nba_service.resolve_pending_bets([self._bet("under", 210)])[0]
        self.assertEqual(outcome, "lose")

    def test_resolve_push_exact_match(self):
        with patch("app.services.nba_service.fetch_espn_scoreboard", return_value=self._game(total=210)):
            _, outcome, _ = nba_service.resolve_pending_bets([self._bet("over", 210)])[0]
        self.assertEqual(outcome, "push")

    def test_resolve_game_not_final_skipped(self):
        with patch("app.services.nba_service.fetch_espn_scoreboard",
                   return_value=self._game(status="STATUS_IN_PROGRESS", total=100)):
            results = nba_service.resolve_pending_bets([self._bet()])
        self.assertEqual(results, [])

    def test_resolve_game_not_found(self):
        bet = self._bet()
        bet.external_game_id = "unknown"
        with patch("app.services.nba_service.fetch_espn_scoreboard", return_value=self._game()):
            results = nba_service.resolve_pending_bets([bet])
        self.assertEqual(results, [])

    def test_resolve_no_external_id(self):
        bet = self._bet()
        bet.external_game_id = None
        with patch("app.services.nba_service.fetch_espn_scoreboard", return_value=self._game()):
            results = nba_service.resolve_pending_bets([bet])
        self.assertEqual(results, [])

    def test_resolve_unknown_bet_type_skipped(self):
        bet = self._bet(bet_type="moneyline")
        with patch("app.services.nba_service.fetch_espn_scoreboard", return_value=self._game()):
            results = nba_service.resolve_pending_bets([bet])
        self.assertEqual(results, [])

    # fetch_odds_events success path
    @patch.dict(os.environ, {"ODDS_API_KEY": "test-key"})
    @patch("app.services.nba_service.requests.get")
    def test_fetch_odds_events_success(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.raise_for_status.return_value = None
        mock_resp.json.return_value = [
            {"home_team": "Los Angeles Lakers", "away_team": "Boston Celtics", "id": "odds_abc"},
            {"home_team": "Golden State Warriors", "away_team": "Miami Heat", "id": "odds_def"},
        ]
        mock_get.return_value = mock_resp

        events = nba_service.fetch_odds_events()
        self.assertEqual(len(events), 2)
        key = nba_service._matchup_key("Los Angeles Lakers", "Boston Celtics")
        self.assertEqual(events[key], "odds_abc")

    # fetch_player_props_for_event — full parsing loop
    @patch.dict(os.environ, {"ODDS_API_KEY": "test-key"})
    @patch("app.services.nba_service.requests.get")
    def test_fetch_player_props_full_success(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.raise_for_status.return_value = None
        mock_resp.json.return_value = {
            "bookmakers": [{
                "markets": [{
                    "key": "player_points",
                    "outcomes": [
                        {"description": "LeBron James", "name": "Over",  "price": -115, "point": 25.5},
                        {"description": "LeBron James", "name": "Under", "price": -105, "point": 25.5},
                        {"description": "Anthony Davis", "name": "Over",  "price": -110, "point": 22.5},
                        {"description": "Anthony Davis", "name": "Under", "price": -110, "point": 22.5},
                    ],
                }, {
                    "key": "player_points_rebounds_assists",
                    "outcomes": [
                        {"description": "LeBron James", "name": "Over",  "price": -120, "point": 40.5},
                        {"description": "LeBron James", "name": "Under", "price": 100, "point": 40.5},
                    ],
                }],
            }],
        }
        mock_get.return_value = mock_resp

        props = nba_service.fetch_player_props_for_event("event123")
        self.assertIn("player_points", props)
        players = [p["player"] for p in props["player_points"]]
        self.assertEqual(players, ["Anthony Davis", "LeBron James"])
        lebron = next(p for p in props["player_points"] if p["player"] == "LeBron James")
        self.assertAlmostEqual(lebron["line"], 25.5)
        self.assertEqual(lebron["over_odds"], -115)
        self.assertEqual(lebron["under_odds"], -105)
        self.assertIn("player_points_rebounds_assists", props)
        self.assertEqual(props["player_points_rebounds_assists"][0]["line"], 40.5)

    @patch.dict(os.environ, {"ODDS_API_KEY": "test-key"})
    @patch("app.services.nba_service.requests.get")
    def test_fetch_player_props_skips_unknown_market(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.raise_for_status.return_value = None
        mock_resp.json.return_value = {
            "bookmakers": [{
                "markets": [{
                    "key": "player_steals",
                    "outcomes": [
                        {"description": "LeBron James", "name": "Over", "price": -110, "point": 1.5},
                    ],
                }],
            }],
        }
        mock_get.return_value = mock_resp
        props = nba_service.fetch_player_props_for_event("event123")
        self.assertEqual(props, {})

    @patch.dict(os.environ, {"ODDS_API_KEY": "test-key"})
    @patch("app.services.nba_service.requests.get")
    def test_fetch_player_props_skips_empty_player_description(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.raise_for_status.return_value = None
        mock_resp.json.return_value = {
            "bookmakers": [{
                "markets": [{
                    "key": "player_points",
                    "outcomes": [
                        {"description": "", "name": "Over", "price": -110, "point": 20.5},
                    ],
                }],
            }],
        }
        mock_get.return_value = mock_resp
        props = nba_service.fetch_player_props_for_event("event123")
        self.assertEqual(props, {})

    @patch.dict(os.environ, {"ODDS_API_KEY": "test-key"})
    @patch("app.services.nba_service.requests.get")
    def test_fetch_player_props_deduplication(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.raise_for_status.return_value = None
        mock_resp.json.return_value = {
            "bookmakers": [
                {
                    "markets": [{"key": "player_points", "outcomes": [
                        {"description": "LeBron James", "name": "Over",  "price": -115, "point": 25.5},
                        {"description": "LeBron James", "name": "Under", "price": -105, "point": 25.5},
                    ]}],
                },
                {
                    "markets": [{"key": "player_points", "outcomes": [
                        {"description": "LeBron James", "name": "Over",  "price": -120, "point": 25.5},
                        {"description": "LeBron James", "name": "Under", "price": -100, "point": 25.5},
                    ]}],
                },
            ],
        }
        mock_get.return_value = mock_resp

        props = nba_service.fetch_player_props_for_event("event123")
        self.assertEqual(len(props["player_points"]), 1)
        self.assertEqual(props["player_points"][0]["over_odds"], -115)

    @patch.dict(os.environ, {"ODDS_API_KEY": "test-key"})
    @patch("app.services.nba_service.requests.get")
    def test_fetch_player_props_skips_no_line(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.raise_for_status.return_value = None
        mock_resp.json.return_value = {
            "bookmakers": [{
                "markets": [{
                    "key": "player_points",
                    "outcomes": [
                        {"description": "Mystery Player", "name": "Over", "price": -110, "point": None},
                    ],
                }],
            }],
        }
        mock_get.return_value = mock_resp
        props = nba_service.fetch_player_props_for_event("event123")
        self.assertEqual(props, {})

    # get_player_props
    @patch("app.services.nba_service.fetch_player_props_for_event", return_value={"player_points": []})
    def test_get_player_props_matching_game(self, mock_fetch):
        games = [{"espn_id": "espn123", "odds_event_id": "odds_abc"}]
        result = nba_service.get_player_props("espn123", games=games)
        mock_fetch.assert_called_once_with("odds_abc")
        self.assertEqual(result, {"player_points": []})

    def test_get_player_props_no_matching_game(self):
        games = [{"espn_id": "different_id", "odds_event_id": "odds_abc"}]
        result = nba_service.get_player_props("espn123", games=games)
        self.assertEqual(result, {})

    @patch("app.services.nba_service.get_todays_games")
    @patch("app.services.nba_service.fetch_player_props_for_event", return_value={})
    def test_get_player_props_fetches_games_when_none(self, _mock_fetch, mock_games):
        mock_games.return_value = [{"espn_id": "espn123", "odds_event_id": "odds_abc"}]
        nba_service.get_player_props("espn123", games=None)
        mock_games.assert_called_once()

    # ── fetch_espn_scoreboard with date_str ───────────────────────────

    @patch("app.services.nba_service.requests.get")
    def test_fetch_espn_scoreboard_with_date_str(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.raise_for_status.return_value = None
        mock_resp.json.return_value = {"events": []}
        mock_get.return_value = mock_resp

        result = nba_service.fetch_espn_scoreboard(date_str="20260225")
        self.assertEqual(result, [])
        self.assertEqual(mock_get.call_args[1]["params"]["dates"], "20260225")

    # ── fetch_espn_boxscore ──────────────────────────────────────────

    @patch("app.services.nba_service.requests.get")
    def test_fetch_espn_boxscore_network_error(self, mock_get):
        mock_get.side_effect = nba_service.requests.RequestException("timeout")
        self.assertEqual(nba_service.fetch_espn_boxscore("espn123"), {})

    @patch("app.services.nba_service.requests.get")
    def test_fetch_espn_boxscore_success(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.raise_for_status.return_value = None
        mock_resp.json.return_value = {
            "boxscore": {
                "players": [{
                    "statistics": [{
                        "names": ["PTS", "REB", "AST"],
                        "athletes": [{
                            "athlete": {"displayName": "LeBron James"},
                            "stats": ["30", "8", "7"],
                        }],
                    }],
                }],
            }
        }
        mock_get.return_value = mock_resp

        result = nba_service.fetch_espn_boxscore("espn123")
        self.assertIn("LeBron James", result)
        self.assertAlmostEqual(result["LeBron James"]["player_points"], 30.0)

    @patch("app.services.nba_service.requests.get")
    def test_fetch_espn_boxscore_three_point_parsing(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.raise_for_status.return_value = None
        mock_resp.json.return_value = {
            "boxscore": {
                "players": [{
                    "statistics": [{
                        "names": ["PTS", "3PT"],
                        "athletes": [{
                            "athlete": {"displayName": "Steph Curry"},
                            "stats": ["35", "7-12"],
                        }],
                    }],
                }],
            }
        }
        mock_get.return_value = mock_resp

        result = nba_service.fetch_espn_boxscore("espn123")
        self.assertIn("Steph Curry", result)
        self.assertAlmostEqual(result["Steph Curry"]["player_threes"], 7.0)

    @patch("app.services.nba_service.requests.get")
    def test_fetch_espn_boxscore_blocks_steals_parsing(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.raise_for_status.return_value = None
        mock_resp.json.return_value = {
            "boxscore": {
                "players": [{
                    "statistics": [{
                        "names": ["PTS", "BLK", "STL"],
                        "athletes": [{
                            "athlete": {"displayName": "Anthony Davis"},
                            "stats": ["22", "3", "2"],
                        }],
                    }],
                }],
            }
        }
        mock_get.return_value = mock_resp

        result = nba_service.fetch_espn_boxscore("espn123")
        self.assertIn("Anthony Davis", result)
        self.assertAlmostEqual(result["Anthony Davis"]["player_blocks"], 3.0)
        self.assertAlmostEqual(result["Anthony Davis"]["player_steals"], 2.0)

    @patch("app.services.nba_service.requests.get")
    def test_fetch_espn_boxscore_skips_empty_name(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.raise_for_status.return_value = None
        mock_resp.json.return_value = {
            "boxscore": {
                "players": [{
                    "statistics": [{
                        "names": ["PTS"],
                        "athletes": [{
                            "athlete": {"displayName": ""},
                            "stats": ["20"],
                        }],
                    }],
                }],
            }
        }
        mock_get.return_value = mock_resp
        self.assertEqual(nba_service.fetch_espn_boxscore("espn123"), {})

    # ── fetch_upcoming_games ─────────────────────────────────────────

    @patch.dict(os.environ, {}, clear=True)
    @patch("app.services.nba_service.requests.get")
    def test_fetch_upcoming_games_no_api_key(self, mock_get):
        os.environ.pop("ODDS_API_KEY", None)
        # No API key: Odds API is skipped, ESPN fallback is called.
        # Mock ESPN returning no games so the overall result is empty.
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"events": []}
        mock_get.return_value = mock_resp
        self.assertEqual(nba_service.fetch_upcoming_games(), [])

    @patch.dict(os.environ, {"ODDS_API_KEY": "test-key"})
    @patch("app.services.nba_service.requests.get")
    def test_fetch_upcoming_games_network_error(self, mock_get):
        mock_get.side_effect = nba_service.requests.RequestException("timeout")
        self.assertEqual(nba_service.fetch_upcoming_games(), [])

    @patch.dict(os.environ, {"ODDS_API_KEY": "test-key"})
    @patch("app.services.nba_service.requests.get")
    def test_fetch_upcoming_games_success(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.raise_for_status.return_value = None
        mock_resp.json.return_value = [{
            "id": "odds_event_1",
            "home_team": "Los Angeles Lakers",
            "away_team": "Boston Celtics",
            "commence_time": "2026-02-26T19:00:00Z",
            "bookmakers": [{
                "markets": [{
                    "key": "totals",
                    "outcomes": [
                        {"name": "Over", "point": 215.5},
                        {"name": "Under", "point": 215.5},
                    ],
                }],
            }],
        }]
        mock_get.return_value = mock_resp

        games = nba_service.fetch_upcoming_games()
        self.assertEqual(len(games), 1)
        self.assertEqual(games[0]["home"]["name"], "Los Angeles Lakers")
        self.assertAlmostEqual(games[0]["over_under_line"], 215.5)

    # ── resolve_pending_bets — over/under no line ────────────────────

    def test_resolve_over_no_line_skipped(self):
        class FakeBet:
            external_game_id = "espn123"
            bet_type = "over"
            over_under_line = None
            picked_team = None
            is_player_prop = False
            player_name = None
            prop_type = None
            prop_line = None

        game = [{"espn_id": "espn123", "status": "STATUS_FINAL", "total_score": 215}]
        with patch("app.services.nba_service.fetch_espn_scoreboard", return_value=game):
            results = nba_service.resolve_pending_bets([FakeBet()])
        self.assertEqual(results, [])

    # ── resolve_pending_bets — moneyline ─────────────────────────────

    def _ml_game(self, home_score=120, away_score=95, status="STATUS_FINAL"):
        return [{
            "espn_id": "espn123",
            "status": status,
            "total_score": home_score + away_score,
            "home": {"name": "Los Angeles Lakers", "score": home_score},
            "away": {"name": "Boston Celtics", "score": away_score},
        }]

    def _ml_bet(self, picked_team="Los Angeles Lakers"):
        class FakeBet:
            external_game_id = "espn123"
            bet_type = "moneyline"
            over_under_line = None
            is_player_prop = False
            player_name = None
            prop_type = None
            prop_line = None
        fb = FakeBet()
        fb.picked_team = picked_team
        return fb

    def test_resolve_moneyline_picked_team_wins(self):
        with patch("app.services.nba_service.fetch_espn_scoreboard",
                   return_value=self._ml_game(home_score=120, away_score=95)):
            results = nba_service.resolve_pending_bets([self._ml_bet("Los Angeles Lakers")])
        self.assertEqual(len(results), 1)
        _, outcome, _ = results[0]
        self.assertEqual(outcome, "win")

    def test_resolve_moneyline_picked_team_loses(self):
        with patch("app.services.nba_service.fetch_espn_scoreboard",
                   return_value=self._ml_game(home_score=120, away_score=95)):
            results = nba_service.resolve_pending_bets([self._ml_bet("Boston Celtics")])
        _, outcome, _ = results[0]
        self.assertEqual(outcome, "lose")

    def test_resolve_moneyline_away_team_wins(self):
        with patch("app.services.nba_service.fetch_espn_scoreboard",
                   return_value=self._ml_game(home_score=90, away_score=115)):
            results = nba_service.resolve_pending_bets([self._ml_bet("Boston Celtics")])
        _, outcome, _ = results[0]
        self.assertEqual(outcome, "win")

    def test_resolve_moneyline_tie_is_push(self):
        with patch("app.services.nba_service.fetch_espn_scoreboard",
                   return_value=self._ml_game(home_score=100, away_score=100)):
            results = nba_service.resolve_pending_bets([self._ml_bet()])
        _, outcome, _ = results[0]
        self.assertEqual(outcome, "push")

    # ── resolve_pending_bets — player props ──────────────────────────

    def _prop_game(self):
        return [{
            "espn_id": "espn123",
            "status": "STATUS_FINAL",
            "total_score": 215,
        }]

    def _prop_bet(self, bet_type="over", prop_line=25.5):
        class FakeBet:
            external_game_id = "espn123"
            over_under_line = None
            picked_team = None
            is_player_prop = True
            player_name = "LeBron James"
            prop_type = "player_points"
        fb = FakeBet()
        fb.bet_type = bet_type
        fb.prop_line = prop_line
        return fb

    @patch("app.services.nba_service.fetch_espn_boxscore")
    def test_resolve_player_prop_over_win(self, mock_boxscore):
        mock_boxscore.return_value = {"LeBron James": {"player_points": 30.0}}
        with patch("app.services.nba_service.fetch_espn_scoreboard",
                   return_value=self._prop_game()):
            results = nba_service.resolve_pending_bets([self._prop_bet("over", 25.5)])
        self.assertEqual(len(results), 1)
        _, outcome, actual = results[0]
        self.assertEqual(outcome, "win")
        self.assertAlmostEqual(actual, 30.0)

    @patch("app.services.nba_service.fetch_espn_boxscore")
    def test_resolve_player_prop_under_win(self, mock_boxscore):
        mock_boxscore.return_value = {"LeBron James": {"player_points": 20.0}}
        with patch("app.services.nba_service.fetch_espn_scoreboard",
                   return_value=self._prop_game()):
            results = nba_service.resolve_pending_bets([self._prop_bet("under", 25.5)])
        _, outcome, _ = results[0]
        self.assertEqual(outcome, "win")

    @patch("app.services.nba_service.fetch_espn_boxscore")
    def test_resolve_player_prop_push(self, mock_boxscore):
        mock_boxscore.return_value = {"LeBron James": {"player_points": 25.5}}
        with patch("app.services.nba_service.fetch_espn_scoreboard",
                   return_value=self._prop_game()):
            results = nba_service.resolve_pending_bets([self._prop_bet("over", 25.5)])
        _, outcome, _ = results[0]
        self.assertEqual(outcome, "push")

    @patch("app.services.nba_service.fetch_espn_boxscore")
    def test_resolve_player_prop_player_not_found(self, mock_boxscore):
        mock_boxscore.return_value = {"Other Player": {"player_points": 20.0}}
        with patch("app.services.nba_service.fetch_espn_scoreboard",
                   return_value=self._prop_game()):
            results = nba_service.resolve_pending_bets([self._prop_bet()])
        self.assertEqual(results, [])

    @patch("app.services.nba_service.fetch_espn_boxscore")
    def test_resolve_player_prop_missing_player_name_skipped(self, mock_boxscore):
        bet = self._prop_bet()
        bet.player_name = None
        with patch("app.services.nba_service.fetch_espn_scoreboard",
                   return_value=self._prop_game()):
            results = nba_service.resolve_pending_bets([bet])
        self.assertEqual(results, [])
        mock_boxscore.assert_not_called()

    @patch("app.services.nba_service.fetch_espn_boxscore")
    @patch("app.services.nba_service.fetch_espn_scoreboard")
    def test_resolve_pending_bets_uses_match_date_window(self, mock_scoreboard, mock_boxscore):
        class FakeBet:
            external_game_id = "espn123"
            bet_type = "over"
            over_under_line = 210.5
            is_player_prop = False
            picked_team = None
            player_name = None
            prop_type = None
            prop_line = None
            match_date = datetime(2025, 3, 1)

        target_game = {"espn_id": "espn123", "status": "STATUS_FINAL", "total_score": 218}

        def _scoreboard_side_effect(*args, **kwargs):
            date_str = kwargs.get("date_str")
            if date_str == "20250301":
                return [target_game]
            return []

        mock_scoreboard.side_effect = _scoreboard_side_effect
        mock_boxscore.return_value = {}

        results = nba_service.resolve_pending_bets([FakeBet()])

        self.assertEqual(len(results), 1)
        _, outcome, total = results[0]
        self.assertEqual(outcome, "win")
        self.assertEqual(total, 218.0)
        called_dates = {c.kwargs.get("date_str") for c in mock_scoreboard.call_args_list}
        self.assertIn("20250301", called_dates)

    @patch("app.services.nba_service.fetch_espn_scoreboard")
    def test_resolve_pending_bets_without_external_game_id_uses_matchup(self, mock_scoreboard):
        class FakeBet:
            external_game_id = None
            bet_type = "over"
            over_under_line = 210.5
            is_player_prop = False
            picked_team = None
            player_name = None
            prop_type = None
            prop_line = None
            team_a = "Boston Celtics"
            team_b = "Los Angeles Lakers"
            match_date = datetime(2025, 3, 1)

        mock_scoreboard.return_value = [{
            "espn_id": "espn123",
            "status": "STATUS_FINAL",
            "total_score": 218,
            "home": {"name": "Los Angeles Lakers", "score": 112},
            "away": {"name": "Boston Celtics", "score": 106},
            "start_time": "2025-03-01T00:00:00Z",
        }]

        results = nba_service.resolve_pending_bets([FakeBet()])
        self.assertEqual(len(results), 1)
        _, outcome, total = results[0]
        self.assertEqual(outcome, "win")
        self.assertEqual(total, 218.0)

    @patch("app.services.nba_service.fetch_espn_boxscore")
    @patch("app.services.nba_service.fetch_espn_scoreboard")
    def test_resolve_prop_without_external_game_id_uses_matchup(self, mock_scoreboard, mock_boxscore):
        class FakeBet:
            external_game_id = None
            bet_type = "over"
            over_under_line = None
            picked_team = None
            is_player_prop = True
            player_name = "LeBron James"
            prop_type = "player_points"
            prop_line = 25.5
            team_a = "Boston Celtics"
            team_b = "Los Angeles Lakers"
            match_date = datetime(2025, 3, 1)

        mock_scoreboard.return_value = [{
            "espn_id": "espn123",
            "status": "STATUS_FINAL",
            "total_score": 218,
            "home": {"name": "Los Angeles Lakers", "score": 112},
            "away": {"name": "Boston Celtics", "score": 106},
            "start_time": "2025-03-01T00:00:00Z",
        }]
        mock_boxscore.return_value = {"LeBron James": {"player_points": 30.0}}

        results = nba_service.resolve_pending_bets([FakeBet()])
        self.assertEqual(len(results), 1)
        _, outcome, stat = results[0]
        self.assertEqual(outcome, "win")
        self.assertEqual(stat, 30.0)
