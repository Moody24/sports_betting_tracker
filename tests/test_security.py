"""Security-focused tests."""

import os
from datetime import timedelta
from unittest.mock import patch

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

    def test_production_cookie_and_session_contract(self):
        with patch.dict(
            os.environ,
            {
                'SECRET_KEY': 'production-test-secret',
                'FLASK_ENV': 'production',
                'SESSION_IDLE_MINUTES': '30',
                'SESSION_ABSOLUTE_HOURS': '12',
                'REMEMBER_COOKIE_DAYS': '14',
            },
            clear=False,
        ):
            app = create_app(testing=True)

        self.assertTrue(app.config['SESSION_COOKIE_SECURE'])
        self.assertTrue(app.config['SESSION_COOKIE_HTTPONLY'])
        self.assertEqual(app.config['SESSION_COOKIE_SAMESITE'], 'Lax')
        self.assertTrue(app.config['REMEMBER_COOKIE_SECURE'])
        self.assertTrue(app.config['REMEMBER_COOKIE_HTTPONLY'])
        self.assertEqual(app.config['REMEMBER_COOKIE_SAMESITE'], 'Lax')
        self.assertEqual(app.config['REMEMBER_COOKIE_DURATION'], timedelta(days=14))
        self.assertEqual(app.config['PERMANENT_SESSION_LIFETIME'], timedelta(minutes=30))
        self.assertEqual(app.config['SESSION_ABSOLUTE_LIFETIME'], timedelta(hours=12))
        self.assertEqual(app.config['SESSION_PROTECTION'], 'strong')

    def test_production_login_sets_hardened_session_and_remember_cookies(self):
        with patch.dict(
            os.environ,
            {
                'SECRET_KEY': 'production-test-secret',
                'FLASK_ENV': 'production',
            },
            clear=False,
        ):
            app = create_app(testing=True)
        app.config['WTF_CSRF_ENABLED'] = False
        client = app.test_client()
        with app.app_context():
            db.drop_all()
            db.create_all()
            db.session.add(make_user('cookieuser', 'cookie@example.com'))
            db.session.commit()

        response = client.post(
            '/auth/login',
            data={
                'username': 'cookieuser',
                'password': 'password123',
                'remember': 'y',
            },
            follow_redirects=False,
        )
        cookies = response.headers.getlist('Set-Cookie')
        session_cookie = next(value for value in cookies if value.startswith('session='))
        remember_cookie = next(
            value for value in cookies if value.startswith('remember_token=')
        )
        for cookie in (session_cookie, remember_cookie):
            self.assertIn('Secure', cookie)
            self.assertIn('HttpOnly', cookie)
            self.assertIn('SameSite=Lax', cookie)
        self.assertNotIn('password123', ''.join(cookies))
        self.assertNotIn('cookie@example.com', ''.join(cookies))

        logout = client.post('/auth/logout', follow_redirects=False)
        cleared = logout.headers.getlist('Set-Cookie')
        with client.session_transaction() as client_session:
            self.assertNotIn('_user_id', client_session)
        self.assertTrue(
            any(
                value.startswith('remember_token=;') and 'Expires=' in value
                for value in cleared
            )
        )

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
            still_there = db.session.get(Bet, bet_id)
            self.assertIsNotNone(still_there)
