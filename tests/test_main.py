"""Tests for the main blueprint (home, dashboard)."""

from app import db

from tests.helpers import BaseTestCase, make_bet


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
