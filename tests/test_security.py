"""Security-focused tests."""

import os

from app import create_app, db
from app.models import Bet

from tests.helpers import BaseTestCase, make_bet, make_user


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
        with self.app.app_context():
            still_there = Bet.query.get(bet_id)
            self.assertIsNotNone(still_there)
