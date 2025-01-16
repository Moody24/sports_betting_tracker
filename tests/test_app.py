import unittest
from app import create_app, db
from app.models import User, Bet
from flask_login import login_user, current_user

class BettingAppTestCase(unittest.TestCase):

    # Setup test database
    def setUp(self):
        self.app = create_app()
        self.app.config['TESTING'] = True
        self.app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///:memory:'
        self.app.config['WTF_CSRF_ENABLED'] = False  # Disable CSRF for testing
        self.client = self.app.test_client()
        
        with self.app.app_context():
            db.create_all()

    # Teardown database after tests
    def tearDown(self):
        with self.app.app_context():
            db.session.remove()
            db.drop_all()

    # Helper function to log in test user
    def login_test_user(self, username='testuser', password='password123'):
        with self.app.app_context():
            user = User.query.filter_by(username=username).first()
            if user:
                login_user(user)

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

    # Test Logout
    def test_logout(self):
        response = self.client.get('/auth/logout', follow_redirects=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'Login', response.data)  # Ensure redirected to login page

    # Test Protected Routes (Ensure User Stays Logged In)
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
        
        response = self.client.get('/bet/bets', follow_redirects=True)
        self.assertEqual(response.status_code, 200)
        self.assertNotIn(b'Login', response.data)  # Ensure user is not redirected to login page

if __name__ == '__main__':
    unittest.main()
