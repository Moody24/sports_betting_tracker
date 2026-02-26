"""Shared test helpers and base class for all test modules."""

import unittest
from datetime import datetime, timezone

from app import create_app, db
from app.models import Bet, User


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
