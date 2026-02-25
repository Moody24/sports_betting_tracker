import json
import os
import unittest
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

from app import create_app, db
from app.models import Bet, User
from app.services import nba_service


# ── Helpers ──────────────────────────────────────────────────────────


def make_user(username="testuser", email="test@example.com", password="password123"):
    user = User(username=username, email=email)
    user.set_password(password)
    return user


def make_bet(user_id, **kwargs):
    defaults = dict(
        team_a="Lakers",
        team_b="Celtics",
        match_date=datetime(2025, 1, 15, tzinfo=timezone.utc),
        bet_amount=25.0,
        outcome="pending",
        bet_type="moneyline",
    )
    defaults.update(kwargs)
    return Bet(user_id=user_id, **defaults)


# ── Base test case ────────────────────────────────────────────────────


class BaseTestCase(unittest.TestCase):
    def setUp(self):
        self.app = create_app(testing=True)
        self.app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///:memory:"
        self.app.config["WTF_CSRF_ENABLED"] = False
        self.client = self.app.test_client()
        with self.app.app_context():
            db.create_all()

    def tearDown(self):
        with self.app.app_context():
            db.session.remove()
            db.drop_all()

    def register_and_login(
        self,
        username="testuser",
        email="test@example.com",
        password="password123",
    ):
        with self.app.app_context():
            user = make_user(username, email, password)
            db.session.add(user)
            db.session.commit()
            user_id = user.id
        self.client.post(
            "/auth/login",
            data={"username": username, "password": password},
            follow_redirects=True,
        )
        return user_id


# ── Model tests ───────────────────────────────────────────────────────


class TestModels(BaseTestCase):
    """Unit tests for User and Bet models."""

    # User model
    def test_user_password_hashing(self):
        with self.app.app_context():
            user = make_user()
            self.assertTrue(user.check_password("password123"))
            self.assertFalse(user.check_password("wrongpassword"))

    def test_user_repr(self):
        user = make_user()
        self.assertIn("testuser", repr(user))

    def test_user_total_bets(self):
        with self.app.app_context():
            user = make_user()
            db.session.add(user)
            db.session.commit()
            db.session.add_all([
                make_bet(user.id, outcome="win"),
                make_bet(user.id, outcome="lose"),
                make_bet(user.id, outcome="pending"),
            ])
            db.session.commit()
            self.assertEqual(user.total_bets(), 3)

    def test_user_total_amount_wagered(self):
        with self.app.app_context():
            user = make_user()
            db.session.add(user)
            db.session.commit()
            db.session.add_all([
                make_bet(user.id, bet_amount=10.0),
                make_bet(user.id, bet_amount=25.0),
            ])
            db.session.commit()
            self.assertAlmostEqual(user.total_amount_wagered(), 35.0)

    def test_user_total_amount_wagered_empty(self):
        with self.app.app_context():
            user = make_user()
            db.session.add(user)
            db.session.commit()
            self.assertAlmostEqual(user.total_amount_wagered(), 0.0)

    def test_user_net_profit_loss(self):
        with self.app.app_context():
            user = make_user()
            db.session.add(user)
            db.session.commit()
            db.session.add_all([
                make_bet(user.id, bet_amount=20.0, outcome="win"),
                make_bet(user.id, bet_amount=10.0, outcome="lose"),
                make_bet(user.id, bet_amount=5.0, outcome="pending"),
            ])
            db.session.commit()
            # win=+20, lose=-10, pending=0 → net=10
            self.assertAlmostEqual(user.net_profit_loss(), 10.0)

    def test_user_total_wins_losses(self):
        with self.app.app_context():
            user = make_user()
            db.session.add(user)
            db.session.commit()
            db.session.add_all([
                make_bet(user.id, outcome="win"),
                make_bet(user.id, outcome="win"),
                make_bet(user.id, outcome="lose"),
            ])
            db.session.commit()
            self.assertEqual(user.total_wins(), 2)
            self.assertEqual(user.total_losses(), 1)

    # Bet.profit_loss
    def test_bet_profit_loss_win(self):
        b = make_bet(1, bet_amount=50.0, outcome="win")
        self.assertAlmostEqual(b.profit_loss(), 50.0)

    def test_bet_profit_loss_lose(self):
        b = make_bet(1, bet_amount=50.0, outcome="lose")
        self.assertAlmostEqual(b.profit_loss(), -50.0)

    def test_bet_profit_loss_pending(self):
        b = make_bet(1, bet_amount=50.0, outcome="pending")
        self.assertAlmostEqual(b.profit_loss(), 0.0)

    def test_bet_profit_loss_push(self):
        b = make_bet(1, bet_amount=50.0, outcome="push")
        self.assertAlmostEqual(b.profit_loss(), 0.0)

    # Bet.expected_profit_for_win
    def test_expected_profit_positive_odds(self):
        b = make_bet(1, bet_amount=100.0, american_odds=200)
        self.assertAlmostEqual(b.expected_profit_for_win(), 200.0)

    def test_expected_profit_negative_odds(self):
        b = make_bet(1, bet_amount=110.0, american_odds=-110)
        self.assertAlmostEqual(b.expected_profit_for_win(), 100.0)

    def test_expected_profit_no_odds(self):
        b = make_bet(1, bet_amount=50.0, american_odds=None)
        self.assertAlmostEqual(b.expected_profit_for_win(), 50.0)

    # Bet.margin
    def test_bet_margin(self):
        b = make_bet(1, over_under_line=210.5, actual_total=215.0)
        self.assertAlmostEqual(b.margin, 4.5)

    def test_bet_margin_none_when_missing_data(self):
        self.assertIsNone(make_bet(1).margin)

    # Bet.is_player_prop / prop_display
    def test_bet_is_player_prop_true(self):
        b = make_bet(1, player_name="LeBron James", prop_type="player_points")
        self.assertTrue(b.is_player_prop)

    def test_bet_is_player_prop_false(self):
        self.assertFalse(make_bet(1).is_player_prop)

    def test_bet_prop_display(self):
        b = make_bet(
            1,
            player_name="LeBron James",
            prop_type="player_points",
            prop_line=25.5,
            bet_type="over",
        )
        self.assertIn("LeBron James", b.prop_display)
        self.assertIn("Over", b.prop_display)
        self.assertIn("25.5", b.prop_display)

    def test_bet_prop_display_none_when_not_prop(self):
        self.assertIsNone(make_bet(1).prop_display)

    # Bet.is_winning_bet / is_losing_bet
    def test_bet_is_winning_losing(self):
        win = make_bet(1, outcome="win")
        lose = make_bet(1, outcome="lose")
        self.assertTrue(win.is_winning_bet())
        self.assertFalse(win.is_losing_bet())
        self.assertTrue(lose.is_losing_bet())
        self.assertFalse(lose.is_winning_bet())

    # Bet.generate_parlay_id
    def test_generate_parlay_id_unique_and_correct_length(self):
        id1 = Bet.generate_parlay_id()
        id2 = Bet.generate_parlay_id()
        self.assertEqual(len(id1), 16)
        self.assertNotEqual(id1, id2)

    def test_bet_repr(self):
        b = make_bet(1)
        self.assertIn("Lakers", repr(b))


# ── Auth route tests ──────────────────────────────────────────────────


class TestAuthRoutes(BaseTestCase):
    """Tests for the auth blueprint."""

    def test_registration_success(self):
        resp = self.client.post(
            "/auth/register",
            data={
                "username": "newuser",
                "email": "new@example.com",
                "password": "password123",
                "confirm_password": "password123",
            },
            follow_redirects=True,
        )
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b"Registration successful", resp.data)

    def test_duplicate_registration_blocked(self):
        data = {
            "username": "dupeuser",
            "email": "dupe@example.com",
            "password": "password123",
            "confirm_password": "password123",
        }
        self.client.post("/auth/register", data=data, follow_redirects=True)
        resp = self.client.post("/auth/register", data=data, follow_redirects=True)
        self.assertIn(b"already exists", resp.data)

    def test_register_password_too_short(self):
        resp = self.client.post(
            "/auth/register",
            data={
                "username": "shortpw",
                "email": "short@example.com",
                "password": "abc",
                "confirm_password": "abc",
            },
            follow_redirects=True,
        )
        self.assertNotIn(b"Registration successful", resp.data)

    def test_register_mismatched_passwords(self):
        resp = self.client.post(
            "/auth/register",
            data={
                "username": "mismatch",
                "email": "mismatch@example.com",
                "password": "password123",
                "confirm_password": "different456",
            },
            follow_redirects=True,
        )
        self.assertNotIn(b"Registration successful", resp.data)

    def test_login_success(self):
        self.register_and_login()
        resp = self.client.get("/dashboard", follow_redirects=True)
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b"Dashboard", resp.data)

    def test_login_wrong_password(self):
        with self.app.app_context():
            db.session.add(make_user())
            db.session.commit()
        resp = self.client.post(
            "/auth/login",
            data={"username": "testuser", "password": "wrongpassword"},
            follow_redirects=True,
        )
        self.assertIn(b"Login failed", resp.data)

    def test_login_unknown_user(self):
        resp = self.client.post(
            "/auth/login",
            data={"username": "ghost", "password": "password123"},
            follow_redirects=True,
        )
        self.assertIn(b"Login failed", resp.data)

    def test_already_logged_in_register_redirects(self):
        self.register_and_login()
        resp = self.client.get("/auth/register", follow_redirects=True)
        self.assertIn(b"already logged in", resp.data)

    def test_already_logged_in_login_redirects(self):
        self.register_and_login()
        resp = self.client.get("/auth/login", follow_redirects=True)
        self.assertIn(b"already logged in", resp.data)

    def test_logout(self):
        self.register_and_login()
        resp = self.client.post("/auth/logout", follow_redirects=True)
        self.assertEqual(resp.status_code, 200)
        resp2 = self.client.get("/dashboard", follow_redirects=True)
        self.assertIn(b"Login", resp2.data)


# ── Bet route tests ───────────────────────────────────────────────────


class TestBetRoutes(BaseTestCase):
    """Tests for the bet blueprint."""

    def test_bets_list_requires_auth(self):
        resp = self.client.get("/bets", follow_redirects=True)
        self.assertIn(b"Login", resp.data)

    def test_new_bet_form_requires_auth(self):
        resp = self.client.get("/bets/new", follow_redirects=True)
        self.assertIn(b"Login", resp.data)

    def test_create_moneyline_bet(self):
        self.register_and_login()
        resp = self.client.post(
            "/bets/new",
            data={
                "team_a": "Lakers",
                "team_b": "Celtics",
                "match_date": "2025-03-01",
                "bet_amount": "50",
                "bet_type": "moneyline",
                "outcome": "pending",
            },
            follow_redirects=True,
        )
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b"Bet recorded successfully", resp.data)

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

    def test_view_bets_redirects(self):
        self.register_and_login()
        resp = self.client.get("/view_bets", follow_redirects=False)
        self.assertEqual(resp.status_code, 302)

    def test_new_bet_form_prepopulated_from_query_params(self):
        self.register_and_login()
        resp = self.client.get(
            "/bets/new?team_a=Lakers&team_b=Nets&match_date=2025-03-01"
            "&bet_type=over&over_under_line=210.5&game_id=abc123"
        )
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b"Lakers", resp.data)

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
        self.register_and_login()
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

    def test_new_bet_form_invalid_match_date_silently_ignored(self):
        """Malformed match_date query param falls back gracefully (lines 71-72)."""
        self.register_and_login()
        resp = self.client.get("/bets/new?team_a=Lakers&team_b=Nets&match_date=not-a-date")
        self.assertEqual(resp.status_code, 200)
        # Form still renders; bad date is silently skipped

    def test_new_bet_form_invalid_over_under_silently_ignored(self):
        """Malformed over_under_line query param falls back gracefully (lines 78-79)."""
        self.register_and_login()
        resp = self.client.get("/bets/new?bet_type=over&over_under_line=notafloat")
        self.assertEqual(resp.status_code, 200)

    def test_new_bet_with_invalid_prop_line_silently_ignored(self):
        """Non-numeric prop_line in POST body is silently skipped (lines 88-91)."""
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
        """When bets are resolved, outcomes are saved and success flash is shown (lines 147-153)."""
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


# ── Main route tests ──────────────────────────────────────────────────


class TestMainRoutes(BaseTestCase):
    """Tests for the main blueprint."""

    def test_home_page(self):
        resp = self.client.get("/")
        self.assertEqual(resp.status_code, 200)

    def test_dashboard_requires_auth(self):
        resp = self.client.get("/dashboard", follow_redirects=True)
        self.assertIn(b"Login", resp.data)

    def test_dashboard_no_bets(self):
        self.register_and_login()
        resp = self.client.get("/dashboard")
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b"Dashboard", resp.data)

    def test_dashboard_with_bets_shows_stats(self):
        user_id = self.register_and_login()
        with self.app.app_context():
            db.session.add_all([
                make_bet(user_id, bet_amount=20, outcome="win"),
                make_bet(user_id, bet_amount=10, outcome="lose"),
            ])
            db.session.commit()
        resp = self.client.get("/dashboard")
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b"Total Bets", resp.data)

    def test_dashboard_streak_all_wins(self):
        user_id = self.register_and_login()
        with self.app.app_context():
            for _ in range(3):
                db.session.add(make_bet(user_id, outcome="win"))
            db.session.commit()
        resp = self.client.get("/dashboard")
        self.assertIn(b"Win", resp.data)

    def test_dashboard_streak_all_losses(self):
        user_id = self.register_and_login()
        with self.app.app_context():
            for _ in range(2):
                db.session.add(make_bet(user_id, outcome="lose"))
            db.session.commit()
        resp = self.client.get("/dashboard")
        self.assertIn(b"Lose", resp.data)

    def test_dashboard_streak_only_pending_shows_no_streak(self):
        user_id = self.register_and_login()
        with self.app.app_context():
            db.session.add(make_bet(user_id, outcome="pending"))
            db.session.commit()
        resp = self.client.get("/dashboard")
        self.assertIn(b"No streak", resp.data)

    def test_dashboard_streak_breaks_on_mixed(self):
        user_id = self.register_and_login()
        with self.app.app_context():
            # Most recent first (desc order by created_at) — win then lose breaks win streak
            db.session.add(make_bet(user_id, outcome="win"))
            db.session.add(make_bet(user_id, outcome="lose"))
            db.session.commit()
        resp = self.client.get("/dashboard")
        self.assertEqual(resp.status_code, 200)

    def test_dashboard_roi_zero_when_no_bets_wagered(self):
        self.register_and_login()
        resp = self.client.get("/dashboard")
        self.assertEqual(resp.status_code, 200)


# ── NBA service tests ─────────────────────────────────────────────────

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
        # Ensure ODDS_API_KEY is absent
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


# ── Security tests ────────────────────────────────────────────────────


    # fetch_odds_events success path
    @patch.dict(os.environ, {"ODDS_API_KEY": "test-key"})
    @patch("app.services.nba_service.requests.get")
    def test_fetch_odds_events_success(self, mock_get):
        """fetch_odds_events builds an event map from a successful API response (lines 161-174)."""
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
        """Happy path: bookmakers → markets → outcomes parsed into props dict (lines 199-252)."""
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
                }],
            }],
        }
        mock_get.return_value = mock_resp

        props = nba_service.fetch_player_props_for_event("event123")
        self.assertIn("player_points", props)
        players = [p["player"] for p in props["player_points"]]
        # Results sorted alphabetically
        self.assertEqual(players, ["Anthony Davis", "LeBron James"])
        lebron = next(p for p in props["player_points"] if p["player"] == "LeBron James")
        self.assertAlmostEqual(lebron["line"], 25.5)
        self.assertEqual(lebron["over_odds"], -115)
        self.assertEqual(lebron["under_odds"], -105)

    @patch.dict(os.environ, {"ODDS_API_KEY": "test-key"})
    @patch("app.services.nba_service.requests.get")
    def test_fetch_player_props_skips_unknown_market(self, mock_get):
        """Markets not in PLAYER_PROP_MARKETS are ignored."""
        mock_resp = MagicMock()
        mock_resp.raise_for_status.return_value = None
        mock_resp.json.return_value = {
            "bookmakers": [{
                "markets": [{
                    "key": "player_steals",   # not in PLAYER_PROP_MARKETS
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
        """Outcomes with empty description are skipped."""
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
        """Same (market, player) from a second bookmaker is deduplicated away."""
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
                    # Second bookmaker — same player, different odds; should be ignored
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
        self.assertEqual(props["player_points"][0]["over_odds"], -115)  # first bookmaker kept

    @patch.dict(os.environ, {"ODDS_API_KEY": "test-key"})
    @patch("app.services.nba_service.requests.get")
    def test_fetch_player_props_skips_no_line(self, mock_get):
        """Props where both over and under have no point are skipped."""
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
        """Returns props when the ESPN ID matches a supplied game (lines 277-279)."""
        games = [{"espn_id": "espn123", "odds_event_id": "odds_abc"}]
        result = nba_service.get_player_props("espn123", games=games)
        mock_fetch.assert_called_once_with("odds_abc")
        self.assertEqual(result, {"player_points": []})

    def test_get_player_props_no_matching_game(self):
        """Returns {} when no game matches the ESPN ID (line 281)."""
        games = [{"espn_id": "different_id", "odds_event_id": "odds_abc"}]
        result = nba_service.get_player_props("espn123", games=games)
        self.assertEqual(result, {})

    @patch("app.services.nba_service.get_todays_games")
    @patch("app.services.nba_service.fetch_player_props_for_event", return_value={})
    def test_get_player_props_fetches_games_when_none(self, _mock_fetch, mock_games):
        """Calls get_todays_games() when games param is None (lines 274-275)."""
        mock_games.return_value = [{"espn_id": "espn123", "odds_event_id": "odds_abc"}]
        nba_service.get_player_props("espn123", games=None)
        mock_games.assert_called_once()


class TestSecurity(BaseTestCase):
    """Security-focused tests."""

    def test_unauthenticated_bets_redirects_to_login(self):
        resp = self.client.get("/bets", follow_redirects=False)
        self.assertEqual(resp.status_code, 302)
        self.assertIn("/auth/login", resp.headers["Location"])

    def test_unauthenticated_dashboard_redirects(self):
        resp = self.client.get("/dashboard", follow_redirects=False)
        self.assertEqual(resp.status_code, 302)

    def test_unauthenticated_post_new_bet_redirects(self):
        resp = self.client.post("/bets/new", follow_redirects=False)
        self.assertEqual(resp.status_code, 302)

    def test_unauthenticated_delete_bet_redirects(self):
        resp = self.client.post("/delete_bet/1", follow_redirects=False)
        self.assertEqual(resp.status_code, 302)

    def test_unauthenticated_nba_update_results_redirects(self):
        resp = self.client.post("/nba/update-results", follow_redirects=False)
        self.assertEqual(resp.status_code, 302)

    def test_secret_key_raises_when_not_set(self):
        original = os.environ.pop("SECRET_KEY", None)
        try:
            with self.assertRaises(RuntimeError, msg="Should raise without SECRET_KEY"):
                create_app(testing=False)
        finally:
            if original is not None:
                os.environ["SECRET_KEY"] = original

    def test_user_data_isolation(self):
        """User A's bets are not visible to user B."""
        user1_id = self.register_and_login("user1", "u1@example.com")
        with self.app.app_context():
            db.session.add(make_bet(user1_id, team_a="SecretTeamX", team_b="Other"))
            db.session.commit()
        self.client.post("/auth/logout", follow_redirects=True)
        with self.app.app_context():
            db.session.add(make_user("user2", "u2@example.com"))
            db.session.commit()
        self.client.post(
            "/auth/login",
            data={"username": "user2", "password": "password123"},
            follow_redirects=True,
        )
        resp = self.client.get("/bets")
        self.assertNotIn(b"SecretTeamX", resp.data)

    def test_cannot_delete_another_users_bet(self):
        user1_id = self.register_and_login("owner", "owner@example.com")
        with self.app.app_context():
            b = make_bet(user1_id)
            db.session.add(b)
            db.session.commit()
            bet_id = b.id
        self.client.post("/auth/logout", follow_redirects=True)
        with self.app.app_context():
            db.session.add(make_user("attacker", "attacker@example.com"))
            db.session.commit()
        self.client.post(
            "/auth/login",
            data={"username": "attacker", "password": "password123"},
            follow_redirects=True,
        )
        resp = self.client.post(f"/delete_bet/{bet_id}", follow_redirects=True)
        self.assertIn(b"permission to delete", resp.data)
        # Confirm the bet still exists
        with self.app.app_context():
            still_there = Bet.query.get(bet_id)
            self.assertIsNotNone(still_there)


class TestCoverageGap(BaseTestCase):
    """Tests that cover previously untested code paths to reach ≥80% coverage."""

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
        self.register_and_login()
        resp = self.client.post(
            "/bets/parlay",
            json={
                "stake": 25.0,
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

    # ── nba_service: fetch_espn_scoreboard with date_str ─────────────

    @patch("app.services.nba_service.requests.get")
    def test_fetch_espn_scoreboard_with_date_str(self, mock_get):
        """date_str branch passes dates param to the ESPN endpoint (line 53)."""
        mock_resp = MagicMock()
        mock_resp.raise_for_status.return_value = None
        mock_resp.json.return_value = {"events": []}
        mock_get.return_value = mock_resp

        result = nba_service.fetch_espn_scoreboard(date_str="20260225")
        self.assertEqual(result, [])
        self.assertEqual(mock_get.call_args[1]["params"]["dates"], "20260225")

    # ── nba_service: fetch_espn_boxscore ─────────────────────────────

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
        """3PT column 'M-A' format is parsed to made-count."""
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

    # ── nba_service: fetch_upcoming_games ────────────────────────────

    @patch.dict(os.environ, {}, clear=True)
    def test_fetch_upcoming_games_no_api_key(self):
        os.environ.pop("ODDS_API_KEY", None)
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

    # ── nba_service: resolve_pending_bets — over/under no line ───────

    def test_resolve_over_no_line_skipped(self):
        """Over bet with over_under_line=None is skipped (line 461)."""
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

    # ── nba_service: resolve_pending_bets — moneyline ────────────────

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

    # ── nba_service: resolve_pending_bets — player props ─────────────

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


if __name__ == "__main__":
    unittest.main()
