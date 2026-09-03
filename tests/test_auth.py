"""Tests for the auth blueprint."""

import time
from unittest.mock import patch

from app import db
from app.routes.auth import SESSION_STARTED_AT_KEY

from tests.helpers import BaseTestCase, make_user


class TestAuthRoutes(BaseTestCase):
    """Tests for the auth blueprint."""

    def test_registration_success(self):
        resp = self.client.post(
            "/auth/register",
            data={
                "username": "newuser",
                "email": "new@example.com",
                "password": "a-safe-test-passphrase",
                "confirm_password": "a-safe-test-passphrase",
            },
            follow_redirects=True,
        )
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b"Registration successful", resp.data)

    def test_duplicate_registration_blocked(self):
        data = {
            "username": "dupeuser",
            "email": "dupe@example.com",
            "password": "a-safe-test-passphrase",
            "confirm_password": "a-safe-test-passphrase",
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

    def test_register_rejects_common_password(self):
        resp = self.client.post(
            "/auth/register",
            data={
                "username": "commonpw",
                "email": "common@example.com",
                "password": "passwordpassword",
                "confirm_password": "passwordpassword",
            },
            follow_redirects=True,
        )
        self.assertNotIn(b"Registration successful", resp.data)
        self.assertIn(b"too common", resp.data)

    def test_register_accepts_unicode_passphrase(self):
        password = "Möhämoud-safe-🔐-2026"
        resp = self.client.post(
            "/auth/register",
            data={
                "username": "unicodepw",
                "email": "unicode@example.com",
                "password": password,
                "confirm_password": password,
            },
            follow_redirects=True,
        )
        self.assertIn(b"Registration successful", resp.data)

    def test_register_rejects_password_over_resource_limit(self):
        password = "x" * 257
        resp = self.client.post(
            "/auth/register",
            data={
                "username": "longpw",
                "email": "long@example.com",
                "password": password,
                "confirm_password": password,
            },
            follow_redirects=True,
        )
        self.assertNotIn(b"Registration successful", resp.data)
        self.assertIn(b"256 characters", resp.data)

    def test_register_mismatched_passwords(self):
        resp = self.client.post(
            "/auth/register",
            data={
                "username": "mismatch",
                "email": "mismatch@example.com",
                "password": "a-safe-test-passphrase",
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

    @patch("app.routes.auth._maybe_trigger_auto_picks_on_login")
    def test_login_success_triggers_auto_pick_hook(self, mock_hook):
        with self.app.app_context():
            db.session.add(make_user())
            db.session.commit()
        resp = self.client.post(
            "/auth/login",
            data={"username": "testuser", "password": "password123"},
            follow_redirects=True,
        )
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b"Login successful", resp.data)
        mock_hook.assert_called_once()

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

    def test_login_starts_permanent_bounded_session(self):
        self.register_and_login()
        with self.client.session_transaction() as client_session:
            self.assertTrue(client_session.permanent)
            self.assertIsInstance(client_session[SESSION_STARTED_AT_KEY], int)

    def test_absolute_session_lifetime_logs_user_out(self):
        self.register_and_login()
        with self.client.session_transaction() as client_session:
            client_session[SESSION_STARTED_AT_KEY] = int(time.time()) - (13 * 3600)

        resp = self.client.get('/dashboard', follow_redirects=False)
        self.assertEqual(resp.status_code, 302)
        self.assertIn('/auth/login', resp.headers['Location'])

        protected = self.client.get('/dashboard', follow_redirects=False)
        self.assertEqual(protected.status_code, 302)
        self.assertIn('/auth/login', protected.headers['Location'])


    def test_login_form_autocomplete_markup(self):
        resp = self.client.get('/auth/login')
        self.assertIn(b'autocomplete="username"', resp.data)
        self.assertIn(b'autocomplete="current-password"', resp.data)

    def test_register_form_autocomplete_and_email_validation(self):
        resp = self.client.get('/auth/register')
        self.assertIn(b'autocomplete="username"', resp.data)
        self.assertIn(b'autocomplete="email"', resp.data)
        self.assertIn(b'autocomplete="new-password"', resp.data)

        invalid = self.client.post(
            '/auth/register',
            data={
                'username': 'newuser',
                'email': 'not-an-email',
                'password': 'a-safe-test-passphrase',
                'confirm_password': 'a-safe-test-passphrase',
            },
            follow_redirects=True,
        )
        self.assertIn(b'valid email address', invalid.data)
        self.assertIn(b'is-invalid', invalid.data)
