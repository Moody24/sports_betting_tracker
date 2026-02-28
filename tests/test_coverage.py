"""Tests that cover previously untested code paths to reach >=80% coverage."""

import json
from unittest.mock import patch

from app import db
from app.models import Bet

from tests.helpers import BaseTestCase, make_bet


class TestCoverageGap(BaseTestCase):
    """Tests that cover previously untested code paths to reach >=80% coverage."""

    # ── bet route: /nba/upcoming-games ───────────────────────────────

    def test_nba_upcoming_games_requires_auth(self):
        resp = self.client.get("/nba/upcoming-games", follow_redirects=False)
        self.assertEqual(resp.status_code, 302)

    @patch("app.routes.bet.get_todays_games", return_value=[])
    @patch("app.routes.bet.fetch_upcoming_games", return_value=[])
    def test_nba_upcoming_games_empty(self, _mock_upcoming, _mock_today):
        self.register_and_login()
        resp = self.client.get("/nba/upcoming-games")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(json.loads(resp.data), [])

    @patch("app.routes.bet.get_todays_games")
    @patch("app.routes.bet.fetch_upcoming_games")
    def test_nba_upcoming_games_returns_json(self, mock_upcoming, mock_today):
        self.register_and_login()
        mock_today.return_value = [{
            "espn_id": "espn1",
            "home": {"name": "Lakers"},
            "away": {"name": "Celtics"},
            "start_time": "2026-02-25T00:00:00Z",
            "over_under_line": 215.5,
        }]
        mock_upcoming.return_value = [{
            "espn_id": "espn2",
            "home": {"name": "Warriors"},
            "away": {"name": "Heat"},
            "match_date": "2026-02-26",
            "over_under_line": 220.0,
        }]
        resp = self.client.get("/nba/upcoming-games")
        self.assertEqual(resp.status_code, 200)
        data = json.loads(resp.data)
        self.assertEqual(len(data), 2)
        self.assertIn("Lakers", data[0]["label"])
        self.assertIn("Tomorrow", data[1]["label"])

    # ── bet route: /bets/parlay (manual_parlay) ───────────────────────

    def test_manual_parlay_no_body(self):
        self.register_and_login()
        resp = self.client.post(
            "/bets/parlay", data="not-json", content_type="text/plain"
        )
        self.assertEqual(resp.status_code, 400)

    def test_manual_parlay_empty_legs(self):
        self.register_and_login()
        resp = self.client.post("/bets/parlay", json={"stake": 25.0, "legs": []})
        self.assertEqual(resp.status_code, 400)

    def test_manual_parlay_zero_stake(self):
        self.register_and_login()
        resp = self.client.post(
            "/bets/parlay",
            json={
                "stake": 0,
                "legs": [{"team_a": "A", "team_b": "B",
                          "match_date": "2026-02-25", "bet_type": "moneyline"}],
            },
        )
        self.assertEqual(resp.status_code, 400)

    def test_manual_parlay_success(self):
        user_id = self.register_and_login()
        resp = self.client.post(
            "/bets/parlay",
            json={
                "stake": 25.0,
                "units": 1.5,
                "legs": [{
                    "team_a": "Lakers",
                    "team_b": "Celtics",
                    "match_date": "2026-02-25",
                    "bet_type": "over",
                    "over_under_line": 215.5,
                    "player_name": "",
                    "prop_type": "",
                    "prop_line": None,
                    "picked_team": "",
                    "game_id": "",
                }],
            },
        )
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(json.loads(resp.data)["success"])
        with self.app.app_context():
            bet = Bet.query.filter_by(user_id=user_id).order_by(Bet.id.desc()).first()
            self.assertEqual(bet.units, 1.5)

    def test_manual_parlay_invalid_date_uses_fallback(self):
        self.register_and_login()
        resp = self.client.post(
            "/bets/parlay",
            json={
                "stake": 10.0,
                "legs": [{"team_a": "A", "team_b": "B",
                          "match_date": "bad-date", "bet_type": "moneyline"}],
            },
        )
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(json.loads(resp.data)["success"])

    def test_manual_parlay_with_player_prop_leg(self):
        """prop_line float parsing and ou_line skip when player_name present."""
        self.register_and_login()
        resp = self.client.post(
            "/bets/parlay",
            json={
                "stake": 15.0,
                "legs": [{
                    "team_a": "Lakers",
                    "team_b": "Celtics",
                    "match_date": "2026-02-25",
                    "bet_type": "over",
                    "over_under_line": 215.5,
                    "player_name": "LeBron James",
                    "prop_type": "player_points",
                    "prop_line": 25.5,
                    "picked_team": "",
                    "game_id": "",
                }],
            },
        )
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(json.loads(resp.data)["success"])

    def test_manual_parlay_missing_teams_returns_400(self):
        """Legs without team_a/team_b are rejected by validation."""
        self.register_and_login()
        resp = self.client.post(
            "/bets/parlay",
            json={
                "stake": 25.0,
                "legs": [{"match_date": "2026-02-25", "bet_type": "moneyline"}],
            },
        )
        self.assertEqual(resp.status_code, 400)
        self.assertIn("team_a", json.loads(resp.data)["error"])

    # ── bet route: parlay grouping in bet list ────────────────────────

    def test_bets_list_parlay_all_wins(self):
        user_id = self.register_and_login()
        with self.app.app_context():
            pid = "parlayWin0001"
            db.session.add(make_bet(user_id, outcome="win", is_parlay=True, parlay_id=pid))
            db.session.add(make_bet(user_id, outcome="win", is_parlay=True, parlay_id=pid))
            db.session.commit()
        resp = self.client.get("/bets")
        self.assertEqual(resp.status_code, 200)

    def test_bets_list_parlay_any_lose(self):
        user_id = self.register_and_login()
        with self.app.app_context():
            pid = "parlayLose001"
            db.session.add(make_bet(user_id, outcome="win", is_parlay=True, parlay_id=pid))
            db.session.add(make_bet(user_id, outcome="lose", is_parlay=True, parlay_id=pid))
            db.session.commit()
        resp = self.client.get("/bets")
        self.assertEqual(resp.status_code, 200)
