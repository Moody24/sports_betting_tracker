import unittest
from app import create_app, db
from app.models import User, Bet
from flask_login import current_user

class BettingAppTestCase(unittest.TestCase):

    # Setup test database
    def setUp(self):
        self.app = create_app()
        self.app.config['TESTING'] = True
        self.app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///:memory:'
        self.client = self.app.test_client()
        
        with self.app.app_context():
            db.create_all()

    # Teardown database after tests
    def tearDown(self):
        with self.app.app_context():
            db.session.remove()
            db.drop_all()

    # Test User Registration
    def test_user_registration(self):
        response = self.client.post('/auth/register', data={
            'username': 'testuser',
            'email': 'testuser@example.com',
            'password': 'password123',
            'confirm_password': 'password123'
        }, follow_redirects=True)
        
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'Registration successful', response.data)

    # Test Login with Correct Credentials
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

    # Test Login with Wrong Credentials
    def test_wrong_login(self):
        response = self.client.post('/auth/login', data={
            'username': 'wronguser',
            'password': 'wrongpass'
        }, follow_redirects=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn(b'Login failed', response.data)

    # Test Logout
    def test_logout(self):
        response = self.client.get('/auth/logout', follow_redirects=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'Logged out successfully', response.data)

    # Test Bet Placement
    def test_place_bet(self):
        with self.app.app_context():
            user = User(username='bettor', email='bettor@example.com')
            user.set_password('password123')
            db.session.add(user)
            db.session.commit()
        
        self.client.post('/auth/login', data={
            'username': 'bettor',
            'password': 'password123'
        }, follow_redirects=True)

        response = self.client.post('/bet/bets', data={
            'team_a': 'Team X',
            'team_b': 'Team Y',
            'match_date': '2024-06-10',
            'bet_amount': '50',
            'outcome': 'pending'
        }, follow_redirects=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn(b'Bet recorded successfully', response.data)

    # Test Protected Routes (Should Require Login)
    def test_protected_routes(self):
        response = self.client.get('/bet/bets', follow_redirects=True)
        self.assertIn(b'Login', response.data)  # Redirects to login

if __name__ == '__main__':
    unittest.main()
