import io
import unittest
from datetime import datetime

from app import create_app, db
from app.models import User, Bet


class BettingAppTestCase(unittest.TestCase):

    def setUp(self):
        self.app = create_app()
        self.app.config['TESTING'] = True
        self.app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///:memory:'
        self.app.config['WTF_CSRF_ENABLED'] = False
        self.client = self.app.test_client()

        with self.app.app_context():
            db.create_all()

    def tearDown(self):
        with self.app.app_context():
            db.session.remove()
            db.drop_all()

    def test_user_registration(self):
        response = self.client.post('/auth/register', data={
            'username': 'testuser',
            'email': 'testuser@example.com',
            'password': 'password123',
            'confirm_password': 'password123'
        }, follow_redirects=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn(b'Registration successful', response.data)

    def test_duplicate_registration_blocked(self):
        self.client.post('/auth/register', data={
            'username': 'testuser',
            'email': 'testuser@example.com',
            'password': 'password123',
            'confirm_password': 'password123'
        }, follow_redirects=True)

        response = self.client.post('/auth/register', data={
            'username': 'testuser',
            'email': 'testuser@example.com',
            'password': 'password123',
            'confirm_password': 'password123'
        }, follow_redirects=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn(b'already exists', response.data)

    def test_user_login(self):
        with self.app.app_context():
            user = User(username='testuser', email='testuser@example.com')
            user.set_password('password123')
            db.session.add(user)
            db.session.commit()

        response = self.client.post('/auth/login', data={
            'username': 'testuser',
            'password': 'password123'
        }, follow_redirects=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn(b'Login successful', response.data)

    def test_logout(self):
        with self.app.app_context():
            user = User(username='logoutuser', email='logout@example.com')
            user.set_password('password123')
            db.session.add(user)
            db.session.commit()

        self.client.post('/auth/login', data={
            'username': 'logoutuser',
            'password': 'password123'
        }, follow_redirects=True)

        response = self.client.post('/auth/logout', follow_redirects=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'Login', response.data)

    def test_protected_routes(self):
        with self.app.app_context():
            user = User(username='testuser', email='testuser@example.com')
            user.set_password('password123')
            db.session.add(user)
            db.session.commit()

        self.client.post('/auth/login', data={
            'username': 'testuser',
            'password': 'password123'
        }, follow_redirects=True)

        response = self.client.get('/bets', follow_redirects=True)
        self.assertEqual(response.status_code, 200)
        self.assertNotIn(b'Login', response.data)

    def test_dashboard_renders_stats(self):
        with self.app.app_context():
            user = User(username='testuser', email='testuser@example.com')
            user.set_password('password123')
            db.session.add(user)
            db.session.commit()

            db.session.add_all([
                Bet(user_id=user.id, team_a='A', team_b='B', match_date=datetime(2025, 1, 1), bet_amount=20, outcome='win'),
                Bet(user_id=user.id, team_a='C', team_b='D', match_date=datetime(2025, 1, 2), bet_amount=10, outcome='lose'),
            ])
            db.session.commit()

        self.client.post('/auth/login', data={
            'username': 'testuser',
            'password': 'password123'
        }, follow_redirects=True)

        response = self.client.get('/dashboard', follow_redirects=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'Your Dashboard', response.data)
        self.assertIn(b'Total Bets', response.data)


if __name__ == '__main__':
    unittest.main()
