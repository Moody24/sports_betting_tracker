"""Tests for the auth blueprint."""

from unittest.mock import patch

from app import db

from tests.helpers import BaseTestCase, make_user


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
                'password': 'password123',
                'confirm_password': 'password123',
            },
            follow_redirects=True,
        )
        self.assertIn(b'valid email address', invalid.data)
        self.assertIn(b'is-invalid', invalid.data)
