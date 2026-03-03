"""Tests for the bet blueprint."""

import json
from datetime import datetime, date
from unittest.mock import patch

from app import db
from app.models import Bet, User, PickContext, GameSnapshot
from app.services.nba_service import APP_TIMEZONE as NBA_APP_TIMEZONE

from tests.helpers import BaseTestCase, make_bet, make_user


class TestBetRoutes(BaseTestCase):
    """Tests for the bet blueprint."""

    @staticmethod
    def _et_today():
        return datetime.now(NBA_APP_TIMEZONE).date()

    def test_bets_list_requires_auth(self):
        resp = self.client.get("/bets", follow_redirects=True)
        self.assertIn(b"Login", resp.data)

    def test_new_bet_form_requires_auth(self):
        resp = self.client.get("/bets/new", follow_redirects=True)
        self.assertIn(b"Login", resp.data)

    def test_new_bet_form_has_moneyline_winner_dropdown(self):
        self.register_and_login()
        resp = self.client.get("/bets/new")
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b'id="single-picked-team"', resp.data)
        self.assertIn(b'Select winner', resp.data)

    def test_new_bet_form_has_ocr_moneyline_winner_dropdown(self):
        self.register_and_login()
        resp = self.client.get("/bets/new")
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b'id="ocr-picked-team"', resp.data)
        self.assertIn(b'Select winner', resp.data)

    def test_new_bet_form_has_parlay_grouped_props_browser_controls(self):
        self.register_and_login()
        resp = self.client.get("/bets/new")
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b'id="load-parlay-props-btn"', resp.data)
        self.assertIn(b'id="parlay-props-browser"', resp.data)

    def test_new_bet_form_shows_units_inputs_when_unit_size_set(self):
        user_id = self.register_and_login()
        with self.app.app_context():
            user = db.session.get(User, user_id)
            user.unit_size = 25.0
            db.session.commit()
        resp = self.client.get("/bets/new")
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b'id="single-units"', resp.data)
        self.assertIn(b'id="prop-units"', resp.data)
        self.assertIn(b'id="parlay-units"', resp.data)

    def test_create_moneyline_bet(self):
        user_id = self.register_and_login()
        resp = self.client.post(
            "/bets/new",
            data={
                "team_a": "Lakers",
                "team_b": "Celtics",
                "match_date": "2025-03-01",
                "bet_amount": "50",
                "bet_type": "moneyline",
                "picked_team": "Lakers",
                "outcome": "pending",
            },
            follow_redirects=True,
        )
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b"Bet recorded successfully", resp.data)
        with self.app.app_context():
            bet = Bet.query.filter_by(user_id=user_id).order_by(Bet.id.desc()).first()
            self.assertEqual(bet.picked_team, "Lakers")

    def test_create_over_under_bet(self):
        self.register_and_login()
        resp = self.client.post(
            "/bets/new",
            data={
                "team_a": "Warriors",
                "team_b": "Nets",
                "match_date": "2025-03-01",
                "bet_amount": "25",
                "bet_type": "over",
                "over_under_line": "215.5",
                "outcome": "pending",
            },
            follow_redirects=True,
        )
        self.assertIn(b"Bet recorded successfully", resp.data)

    def test_new_bet_total_falls_back_to_prop_line_when_over_under_missing(self):
        user_id = self.register_and_login()
        self.client.post(
            "/bets/new",
            data={
                "team_a": "Warriors",
                "team_b": "Nets",
                "match_date": "2025-03-01",
                "bet_amount": "25",
                "bet_type": "over",
                "prop_line": "218.5",
                "outcome": "pending",
            },
            follow_redirects=True,
        )
        with self.app.app_context():
            bet = Bet.query.filter_by(user_id=user_id).order_by(Bet.id.desc()).first()
            self.assertIsNotNone(bet)
            self.assertEqual(bet.over_under_line, 218.5)
            self.assertIsNone(bet.prop_line)

    def test_new_bet_saves_units_when_provided(self):
        user_id = self.register_and_login()
        self.client.post(
            "/bets/new",
            data={
                "team_a": "Warriors",
                "team_b": "Nets",
                "match_date": "2025-03-01",
                "bet_amount": "25",
                "units": "1.0",
                "bet_type": "over",
                "over_under_line": "215.5",
                "outcome": "pending",
            },
            follow_redirects=True,
        )
        with self.app.app_context():
            bet = Bet.query.filter_by(user_id=user_id).order_by(Bet.id.desc()).first()
            self.assertEqual(bet.units, 1.0)

    def test_new_bet_player_prop_uses_prop_line_not_total_line(self):
        user_id = self.register_and_login()
        self.client.post(
            "/bets/new",
            data={
                "team_a": "Warriors",
                "team_b": "Nets",
                "match_date": "2025-03-01",
                "bet_amount": "25",
                "bet_type": "over",
                "player_name": "Stephen Curry",
                "prop_type": "player_points",
                "prop_line": "29.5",
                "outcome": "pending",
            },
            follow_redirects=True,
        )
        with self.app.app_context():
            bet = Bet.query.filter_by(user_id=user_id).order_by(Bet.id.desc()).first()
            self.assertIsNotNone(bet)
            self.assertEqual(bet.prop_line, 29.5)
            self.assertIsNone(bet.over_under_line)
            self.assertIsNotNone(PickContext.query.filter_by(bet_id=bet.id).first())

    def test_new_bet_over_under_without_any_line_is_rejected(self):
        user_id = self.register_and_login()
        resp = self.client.post(
            "/bets/new",
            data={
                "team_a": "Warriors",
                "team_b": "Nets",
                "match_date": "2025-03-01",
                "bet_amount": "25",
                "bet_type": "over",
                "outcome": "pending",
            },
            follow_redirects=False,
        )
        self.assertEqual(resp.status_code, 400)
        self.assertIn(b"line is required for totals", resp.data)
        with self.app.app_context():
            count = Bet.query.filter_by(user_id=user_id).count()
            self.assertEqual(count, 0)

    def test_bets_list_shows_own_bets(self):
        user_id = self.register_and_login()
        with self.app.app_context():
            db.session.add(make_bet(user_id, team_a="Lakers", team_b="Celtics"))
            db.session.commit()
        resp = self.client.get("/bets")
        self.assertIn(b"Lakers", resp.data)

    def test_bets_list_filter_by_status(self):
        user_id = self.register_and_login()
        with self.app.app_context():
            db.session.add(make_bet(user_id, outcome="win"))
            db.session.add(make_bet(user_id, outcome="lose"))
            db.session.commit()
        resp = self.client.get("/bets?status=win")
        self.assertEqual(resp.status_code, 200)

    def test_bets_list_filter_by_search(self):
        user_id = self.register_and_login()
        with self.app.app_context():
            db.session.add(make_bet(user_id, team_a="GoldenState", team_b="Miami"))
            db.session.commit()
        resp = self.client.get("/bets?q=GoldenState")
        self.assertIn(b"GoldenState", resp.data)

    def test_bets_list_filter_by_date_range(self):
        user_id = self.register_and_login()
        with self.app.app_context():
            db.session.add(make_bet(user_id, match_date=datetime(2025, 1, 10)))
            db.session.commit()
        resp = self.client.get("/bets?start_date=2025-01-01&end_date=2025-01-31")
        self.assertEqual(resp.status_code, 200)

    def test_bets_list_invalid_date_handled_gracefully(self):
        self.register_and_login()
        resp = self.client.get("/bets?start_date=not-a-date&end_date=also-bad")
        self.assertEqual(resp.status_code, 200)

    def test_delete_own_bet(self):
        user_id = self.register_and_login()
        with self.app.app_context():
            b = make_bet(user_id)
            db.session.add(b)
            db.session.commit()
            bet_id = b.id
        resp = self.client.post(f"/delete_bet/{bet_id}", follow_redirects=True)
        self.assertIn(b"deleted successfully", resp.data)

    def test_delete_other_users_bet_blocked(self):
        user1_id = self.register_and_login("user1", "u1@example.com")
        with self.app.app_context():
            b = make_bet(user1_id)
            db.session.add(b)
            db.session.commit()
            bet_id = b.id
        self.client.post("/auth/logout", follow_redirects=True)
        with self.app.app_context():
            db.session.add(make_user("user2", "u2@example.com"))
            db.session.commit()
        self.client.post(
            "/auth/login",
            data={"username": "user2", "password": "password123"},
            follow_redirects=True,
        )
        resp = self.client.post(f"/delete_bet/{bet_id}", follow_redirects=True)
        self.assertIn(b"permission to delete", resp.data)

    def test_delete_nonexistent_bet_returns_404(self):
        self.register_and_login()
        resp = self.client.post("/delete_bet/99999", follow_redirects=True)
        self.assertEqual(resp.status_code, 404)

    def test_new_bet_form_prepopulated_from_query_params(self):
        self.register_and_login()
        resp = self.client.get(
            "/bets/new?team_a=Lakers&team_b=Nets&match_date=2025-03-01"
            "&bet_type=over&over_under_line=210.5&game_id=abc123"
        )
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b"Lakers", resp.data)

    def test_new_bet_form_prepopulated_prop_from_query_params(self):
        self.register_and_login()
        resp = self.client.get(
            "/bets/new?team_a=Lakers&team_b=Nets&match_date=2025-03-01"
            "&bet_type=under&player_name=LeBron+James"
            "&prop_type=player_points&prop_line=27.5&game_id=abc123"
        )
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b'id="prop-player-name"', resp.data)
        self.assertIn(b'value="LeBron James"', resp.data)
        self.assertIn(b'value="27.5"', resp.data)

    def test_nba_today_requires_auth(self):
        resp = self.client.get("/nba/today", follow_redirects=True)
        self.assertIn(b"Login", resp.data)

    @patch("app.routes.bet.get_todays_games", return_value=[])
    def test_nba_today_renders(self, _mock):
        self.register_and_login()
        resp = self.client.get("/nba/today")
        self.assertEqual(resp.status_code, 200)

    def test_nba_update_results_no_pending(self):
        self.register_and_login()
        resp = self.client.post("/nba/update-results", follow_redirects=True)
        self.assertIn(b"No pending bets", resp.data)

    @patch("app.routes.bet.resolve_pending_bets", return_value=[])
    def test_nba_update_results_with_pending_none_resolved(self, _mock):
        user_id = self.register_and_login()
        with self.app.app_context():
            db.session.add(
                make_bet(
                    user_id,
                    bet_type="over",
                    over_under_line=210.5,
                    external_game_id="game1",
                    outcome="pending",
                )
            )
            db.session.commit()
        resp = self.client.post("/nba/update-results", follow_redirects=True)
        self.assertIn(b"No pending bets", resp.data)

    @patch("app.routes.bet.get_player_props", return_value={})
    def test_nba_props_returns_json(self, _mock):
        self.register_and_login()
        resp = self.client.get("/nba/props/espn123")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.content_type, "application/json")

    @patch("app.routes.bet.get_player_props", return_value={})
    def test_nba_props_returns_cached_snapshot_when_live_empty(self, _mock):
        self.register_and_login()
        with self.app.app_context():
            db.session.add(GameSnapshot(
                espn_id="espn123",
                game_date=self._et_today(),
                home_team="Miami Heat",
                away_team="Houston Rockets",
                home_logo="",
                away_logo="",
                home_score=0,
                away_score=0,
                status="STATUS_SCHEDULED",
                props_json=json.dumps({
                    "player_points": [{
                        "player": "Jimmy Butler",
                        "line": 22.5,
                        "over_odds": -110,
                        "under_odds": -110,
                    }]
                }),
            ))
            db.session.commit()

        resp = self.client.get("/nba/props/espn123")
        self.assertEqual(resp.status_code, 200)
        data = json.loads(resp.data)
        self.assertIn("player_points", data)
        self.assertEqual(data["player_points"][0]["player"], "Jimmy Butler")

    @patch("app.routes.bet.fetch_upcoming_games", return_value=[])
    @patch("app.routes.bet.fetch_player_props_for_event")
    @patch("app.routes.bet.get_todays_games")
    def test_nba_today_prefetches_props_snapshot(self, mock_games, mock_fetch_props, _mock_upcoming):
        self.register_and_login()
        mock_games.return_value = [{
            "espn_id": "espnA",
            "home": {"name": "Charlotte Hornets", "score": 0, "logo": "", "abbr": "CHA"},
            "away": {"name": "Portland Trail Blazers", "score": 0, "logo": "", "abbr": "POR"},
            "status": "STATUS_SCHEDULED",
            "start_time": "2026-02-28T19:00:00Z",
            "over_under_line": 221.5,
            "moneyline_home": -120,
            "moneyline_away": 102,
            "odds_event_id": "evt123",
        }]
        mock_fetch_props.return_value = {
            "player_points": [{
                "player": "LaMelo Ball",
                "line": 24.5,
                "over_odds": -110,
                "under_odds": -110,
            }]
        }

        resp = self.client.get("/nba/today")
        self.assertEqual(resp.status_code, 200)
        with self.app.app_context():
            snap = GameSnapshot.query.filter_by(espn_id="espnA", game_date=self._et_today()).first()
            self.assertIsNotNone(snap)
            self.assertIsNotNone(snap.props_json)

    def test_nba_prop_progress_requires_query_params(self):
        self.register_and_login()
        resp = self.client.get("/nba/prop-progress/game123")
        self.assertEqual(resp.status_code, 400)
        data = json.loads(resp.data)
        self.assertFalse(data["ok"])

    @patch("app.routes.bet.requests.get")
    def test_nba_prop_progress_success(self, mock_get):
        self.register_and_login()
        mock_resp = mock_get.return_value
        mock_resp.raise_for_status.return_value = None
        mock_resp.json.return_value = {
            "boxscore": {
                "players": [{
                    "statistics": [{
                        "names": ["PTS", "AST", "REB", "3PT"],
                        "athletes": [{
                            "athlete": {"displayName": "LeBron James"},
                            "stats": ["22", "7", "8", "2-6"],
                        }],
                    }]
                }]
            },
            "header": {
                "competitions": [{
                    "status": {"type": {"name": "STATUS_IN_PROGRESS", "detail": "Q3 05:12"}}
                }]
            }
        }
        resp = self.client.get(
            "/nba/prop-progress/game123?player=LeBron%20James&prop_type=player_points"
        )
        self.assertEqual(resp.status_code, 200)
        data = json.loads(resp.data)
        self.assertTrue(data["ok"])
        self.assertEqual(data["player"], "LeBron James")
        self.assertEqual(data["stat"], 22.0)
        self.assertIn("STATUS_IN_PROGRESS", data["status"])
        self.assertEqual(mock_get.call_count, 1)

    def test_bets_list_shows_prop_progress_button_for_pending_props(self):
        user_id = self.register_and_login()
        with self.app.app_context():
            db.session.add(
                make_bet(
                    user_id,
                    outcome="pending",
                    bet_type="over",
                    player_name="LeBron James",
                    prop_type="player_points",
                    prop_line=25.5,
                    external_game_id="game123",
                )
            )
            db.session.commit()
        resp = self.client.get("/bets")
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b"Check progress", resp.data)

    # JSON place-bets endpoint
    def test_place_bets_no_body(self):
        self.register_and_login()
        resp = self.client.post("/nba/place-bets", content_type="application/json", data="{}")
        self.assertEqual(resp.status_code, 400)

    def test_place_bets_empty_legs(self):
        self.register_and_login()
        resp = self.client.post(
            "/nba/place-bets",
            content_type="application/json",
            data=json.dumps({"stake": 25, "legs": []}),
        )
        self.assertEqual(resp.status_code, 400)

    def test_place_bets_zero_stake(self):
        self.register_and_login()
        resp = self.client.post(
            "/nba/place-bets",
            content_type="application/json",
            data=json.dumps({
                "stake": 0,
                "legs": [{"player_name": "X", "prop_type": "y", "bet_type": "over",
                           "team_a": "A", "team_b": "B", "match_date": "2025-01-01"}],
            }),
        )
        self.assertEqual(resp.status_code, 400)

    def test_place_bets_single_success(self):
        user_id = self.register_and_login()
        payload = {
            "stake": 25.0,
            "is_parlay": False,
            "legs": [{
                "player_name": "LeBron James",
                "prop_type": "player_points",
                "prop_line": 25.5,
                "bet_type": "over",
                "american_odds": -115,
                "team_a": "Lakers",
                "team_b": "Celtics",
                "game_id": "game123",
                "match_date": "2025-03-01",
            }],
        }
        resp = self.client.post(
            "/nba/place-bets",
            content_type="application/json",
            data=json.dumps(payload),
        )
        self.assertEqual(resp.status_code, 200)
        data = json.loads(resp.data)
        self.assertTrue(data["success"])
        self.assertEqual(data["count"], 1)
        with self.app.app_context():
            bet = Bet.query.filter_by(user_id=user_id).order_by(Bet.id.desc()).first()
            self.assertEqual(bet.prop_line, 25.5)
            self.assertIsNone(bet.over_under_line)
            self.assertIsNotNone(PickContext.query.filter_by(bet_id=bet.id).first())

    def test_place_bets_parlay_success(self):
        self.register_and_login()
        payload = {
            "stake": 10.0,
            "is_parlay": True,
            "legs": [
                {"player_name": "A", "prop_type": "player_points", "prop_line": 20.5,
                 "bet_type": "over", "american_odds": -110,
                 "team_a": "X", "team_b": "Y", "game_id": "g1", "match_date": "2025-03-01"},
                {"player_name": "B", "prop_type": "player_rebounds", "prop_line": 8.5,
                 "bet_type": "under", "american_odds": -110,
                 "team_a": "X", "team_b": "Y", "game_id": "g1", "match_date": "2025-03-01"},
            ],
        }
        resp = self.client.post(
            "/nba/place-bets",
            content_type="application/json",
            data=json.dumps(payload),
        )
        data = json.loads(resp.data)
        self.assertTrue(data["success"])
        self.assertEqual(data["count"], 2)

    def test_place_bets_invalid_date_uses_fallback(self):
        self.register_and_login()
        payload = {
            "stake": 20.0,
            "is_parlay": False,
            "legs": [{
                "player_name": "X", "prop_type": "player_points", "prop_line": 20.0,
                "bet_type": "over", "team_a": "A", "team_b": "B",
                "match_date": "not-a-date",
            }],
        }
        resp = self.client.post(
            "/nba/place-bets",
            content_type="application/json",
            data=json.dumps(payload),
        )
        self.assertEqual(resp.status_code, 200)

    def test_place_bets_missing_teams_returns_400(self):
        """Legs without team_a/team_b are rejected by validation."""
        self.register_and_login()
        payload = {
            "stake": 25.0,
            "legs": [{"player_name": "X", "prop_type": "y", "bet_type": "over",
                       "match_date": "2025-01-01"}],
        }
        resp = self.client.post(
            "/nba/place-bets",
            content_type="application/json",
            data=json.dumps(payload),
        )
        self.assertEqual(resp.status_code, 400)
        self.assertIn("team_a", json.loads(resp.data)["error"])

    def test_place_bets_invalid_stake_string(self):
        """Non-numeric stake returns 400."""
        self.register_and_login()
        resp = self.client.post(
            "/nba/place-bets",
            content_type="application/json",
            data=json.dumps({
                "stake": "not-a-number",
                "legs": [{"team_a": "A", "team_b": "B", "bet_type": "over",
                           "match_date": "2025-01-01"}],
            }),
        )
        self.assertEqual(resp.status_code, 400)
        self.assertIn("number", json.loads(resp.data)["error"])

    def test_new_bet_form_invalid_match_date_silently_ignored(self):
        """Malformed match_date query param falls back gracefully."""
        self.register_and_login()
        resp = self.client.get("/bets/new?team_a=Lakers&team_b=Nets&match_date=not-a-date")
        self.assertEqual(resp.status_code, 200)

    def test_new_bet_form_invalid_over_under_silently_ignored(self):
        """Malformed over_under_line query param falls back gracefully."""
        self.register_and_login()
        resp = self.client.get("/bets/new?bet_type=over&over_under_line=notafloat")
        self.assertEqual(resp.status_code, 200)

    def test_new_bet_with_invalid_prop_line_silently_ignored(self):
        """Non-numeric prop_line in POST body is silently skipped."""
        self.register_and_login()
        resp = self.client.post(
            "/bets/new",
            data={
                "team_a": "Lakers",
                "team_b": "Celtics",
                "match_date": "2025-03-01",
                "bet_amount": "25",
                "bet_type": "moneyline",
                "outcome": "pending",
                "prop_line": "notafloat",
            },
            follow_redirects=True,
        )
        self.assertIn(b"Bet recorded successfully", resp.data)

    @patch("app.routes.bet.resolve_pending_bets")
    def test_nba_update_results_resolves_bets(self, mock_resolve):
        """When bets are resolved, outcomes are saved and success flash is shown."""
        user_id = self.register_and_login()
        with self.app.app_context():
            b = make_bet(
                user_id,
                bet_type="over",
                over_under_line=210.5,
                external_game_id="game1",
                outcome="pending",
            )
            db.session.add(b)
            db.session.commit()
            bet_id = b.id

        # resolve_pending_bets returns a real Bet object so the route can mutate it
        with self.app.app_context():
            real_bet = db.session.get(Bet, bet_id)
            mock_resolve.return_value = [(real_bet, "win", 215.0)]
            resp = self.client.post("/nba/update-results", follow_redirects=True)

        self.assertIn(b"Updated 1 bet", resp.data)

    # ── Export ────────────────────────────────────────────────────────
    def test_export_bets_csv(self):
        user_id = self.register_and_login()
        with self.app.app_context():
            db.session.add(make_bet(user_id, outcome="win"))
            db.session.commit()
        resp = self.client.get("/bets/export")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("text/csv", resp.content_type)

    def test_export_bets_with_filters(self):
        user_id = self.register_and_login()
        with self.app.app_context():
            db.session.add(make_bet(user_id, outcome="win", team_a="Lakers"))
            db.session.commit()
        resp = self.client.get("/bets/export?status=win&q=Lakers")
        self.assertEqual(resp.status_code, 200)

    # ── P2 coverage additions ─────────────────────────────────────────

    def test_bets_list_with_winning_parlay_computes_pl_map(self):
        """parlay_pl_map is built when parlay bets appear in the list."""
        user_id = self.register_and_login()
        pid = "test-parlay-pl"
        with self.app.app_context():
            db.session.add(make_bet(
                user_id, outcome="win", is_parlay=True, parlay_id=pid,
                american_odds=-110,
            ))
            db.session.add(make_bet(
                user_id, outcome="win", is_parlay=True, parlay_id=pid,
                american_odds=-110,
            ))
            db.session.commit()
        resp = self.client.get("/bets")
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b"Parlay", resp.data)

    def test_bets_list_with_losing_parlay_shows_header(self):
        """Losing parlay header renders correctly via parlay_status."""
        user_id = self.register_and_login()
        pid = "test-parlay-lose"
        with self.app.app_context():
            db.session.add(make_bet(
                user_id, outcome="lose", is_parlay=True, parlay_id=pid,
            ))
            db.session.add(make_bet(
                user_id, outcome="win", is_parlay=True, parlay_id=pid,
            ))
            db.session.commit()
        resp = self.client.get("/bets")
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b"leg lost", resp.data)

    def test_new_bet_form_includes_bankroll_var_when_starting_bankroll_set(self):
        """USER_BANKROLL is non-null when user has starting_bankroll configured."""
        user_id = self.register_and_login()
        with self.app.app_context():
            user = db.session.get(User, user_id)
            user.starting_bankroll = 500.0
            db.session.commit()
        resp = self.client.get("/bets/new")
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b"USER_BANKROLL", resp.data)
        self.assertNotIn(b"USER_BANKROLL = null", resp.data)

    def test_new_bet_with_american_odds_saves_value(self):
        """american_odds form field is parsed and stored on the bet."""
        user_id = self.register_and_login()
        self.client.post(
            "/bets/new",
            data={
                "team_a": "Lakers", "team_b": "Celtics",
                "match_date": "2025-03-01", "bet_amount": "25",
                "bet_type": "moneyline", "outcome": "pending",
                "american_odds": "-110",
            },
            follow_redirects=True,
        )
        with self.app.app_context():
            bet = Bet.query.filter_by(user_id=user_id).order_by(Bet.id.desc()).first()
            self.assertEqual(bet.american_odds, -110)

    def test_new_bet_with_invalid_american_odds_ignored(self):
        """Non-integer american_odds falls back to None without error."""
        self.register_and_login()
        resp = self.client.post(
            "/bets/new",
            data={
                "team_a": "Lakers", "team_b": "Celtics",
                "match_date": "2025-03-01", "bet_amount": "25",
                "bet_type": "moneyline", "outcome": "pending",
                "american_odds": "notanint",
            },
            follow_redirects=True,
        )
        self.assertIn(b"Bet recorded successfully", resp.data)

    def test_new_bet_with_invalid_bonus_multiplier_ignored(self):
        """Non-numeric bonus_multiplier falls back to 1.0 without error."""
        self.register_and_login()
        resp = self.client.post(
            "/bets/new",
            data={
                "team_a": "Lakers", "team_b": "Celtics",
                "match_date": "2025-03-01", "bet_amount": "25",
                "bet_type": "moneyline", "outcome": "pending",
                "bonus_multiplier": "notafloat",
            },
            follow_redirects=True,
        )
        self.assertIn(b"Bet recorded successfully", resp.data)

    def test_new_bet_player_prop_without_prop_line_rejected(self):
        """Player prop (over) with no prop_line is rejected with 400."""
        user_id = self.register_and_login()
        resp = self.client.post(
            "/bets/new",
            data={
                "team_a": "Warriors", "team_b": "Nets",
                "match_date": "2025-03-01", "bet_amount": "25",
                "bet_type": "over",
                "player_name": "Stephen Curry",
                "prop_type": "player_points",
                "outcome": "pending",
                # no prop_line
            },
            follow_redirects=False,
        )
        self.assertEqual(resp.status_code, 400)
        self.assertIn(b"prop line is required", resp.data)
        with self.app.app_context():
            self.assertEqual(Bet.query.filter_by(user_id=user_id).count(), 0)

    def test_new_bet_with_invalid_units_ignored(self):
        """Non-numeric units value is silently skipped."""
        self.register_and_login()
        resp = self.client.post(
            "/bets/new",
            data={
                "team_a": "Lakers", "team_b": "Celtics",
                "match_date": "2025-03-01", "bet_amount": "25",
                "bet_type": "moneyline", "outcome": "pending",
                "units": "notafloat",
            },
            follow_redirects=True,
        )
        self.assertIn(b"Bet recorded successfully", resp.data)

    @patch("app.routes.bet.requests.get", side_effect=Exception("network error"))
    def test_nba_prop_progress_request_exception_returns_200(self, _mock):
        """Network error from ESPN returns 200 with game_not_started status."""
        self.register_and_login()
        resp = self.client.get(
            "/nba/prop-progress/game123?player=LeBron%20James&prop_type=player_points"
        )
        self.assertEqual(resp.status_code, 200)
        data = json.loads(resp.data)
        self.assertFalse(data["ok"])
        self.assertEqual(data["status"], "game_not_started")

    @patch("app.routes.bet.requests.get")
    def test_nba_prop_progress_empty_boxscore_returns_200(self, mock_get):
        """ESPN response with no boxscore players returns 200 with game_not_started."""
        self.register_and_login()
        mock_resp = mock_get.return_value
        mock_resp.raise_for_status.return_value = None
        mock_resp.json.return_value = {"boxscore": {"players": []}, "header": {}}
        resp = self.client.get(
            "/nba/prop-progress/game123?player=LeBron%20James&prop_type=player_points"
        )
        self.assertEqual(resp.status_code, 200)
        data = json.loads(resp.data)
        self.assertFalse(data["ok"])
        self.assertEqual(data["status"], "game_not_started")
