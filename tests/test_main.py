"""Tests for the main blueprint (home, dashboard)."""

from app import db
from app.models import User

from tests.helpers import BaseTestCase, make_bet


class TestMainRoutes(BaseTestCase):
    """Tests for the main blueprint."""

    def test_home_page(self):
        resp = self.client.get("/")
        self.assertEqual(resp.status_code, 200)

    def test_dashboard_requires_auth(self):
        resp = self.client.get("/dashboard", follow_redirects=True)
        self.assertIn(b"Login", resp.data)

    def test_dashboard_settings_requires_auth(self):
        resp = self.client.post("/dashboard/settings", data={"unit_size": "25"}, follow_redirects=False)
        self.assertEqual(resp.status_code, 302)

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

    def test_dashboard_settings_saves_unit_size(self):
        user_id = self.register_and_login()
        resp = self.client.post(
            "/dashboard/settings",
            data={"unit_size": "25.5"},
            follow_redirects=True,
        )
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b"Unit size saved", resp.data)
        with self.app.app_context():
            user = db.session.get(User, user_id)
            self.assertEqual(user.unit_size, 25.5)

    def test_dashboard_settings_clears_unit_size(self):
        user_id = self.register_and_login()
        with self.app.app_context():
            user = db.session.get(User, user_id)
            user.unit_size = 30.0
            db.session.commit()
        resp = self.client.post(
            "/dashboard/settings",
            data={"unit_size": ""},
            follow_redirects=True,
        )
        self.assertEqual(resp.status_code, 200)
        with self.app.app_context():
            user = db.session.get(User, user_id)
            self.assertIsNone(user.unit_size)

    def test_dashboard_settings_rejects_invalid_unit_size(self):
        user_id = self.register_and_login()
        resp = self.client.post(
            "/dashboard/settings",
            data={"unit_size": "-5"},
            follow_redirects=True,
        )
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b"greater than zero", resp.data)
        with self.app.app_context():
            user = db.session.get(User, user_id)
            self.assertIsNone(user.unit_size)


    def test_home_has_skip_link_and_main_target(self):
        resp = self.client.get('/')
        self.assertIn(b'Skip to main content', resp.data)
        self.assertIn(b'id="main-content"', resp.data)
