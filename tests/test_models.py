"""Unit tests for User and Bet models."""

from app import db
from app.models import Bet

from tests.helpers import BaseTestCase, make_bet, make_user


class TestModels(BaseTestCase):
    """Unit tests for User and Bet models."""

    # User model
    def test_user_password_hashing(self):
        with self.app.app_context():
            user = make_user()
            self.assertTrue(user.check_password("password123"))
            self.assertFalse(user.check_password("wrongpassword"))

    def test_user_repr(self):
        user = make_user()
        self.assertIn("testuser", repr(user))

    def test_user_total_bets(self):
        with self.app.app_context():
            user = make_user()
            db.session.add(user)
            db.session.commit()
            db.session.add_all([
                make_bet(user.id, outcome="win"),
                make_bet(user.id, outcome="lose"),
                make_bet(user.id, outcome="pending"),
            ])
            db.session.commit()
            self.assertEqual(user.total_bets(), 3)

    def test_user_total_amount_wagered(self):
        with self.app.app_context():
            user = make_user()
            db.session.add(user)
            db.session.commit()
            db.session.add_all([
                make_bet(user.id, bet_amount=10.0),
                make_bet(user.id, bet_amount=25.0),
            ])
            db.session.commit()
            self.assertAlmostEqual(user.total_amount_wagered(), 35.0)

    def test_user_total_amount_wagered_empty(self):
        with self.app.app_context():
            user = make_user()
            db.session.add(user)
            db.session.commit()
            self.assertAlmostEqual(user.total_amount_wagered(), 0.0)

    def test_user_net_profit_loss(self):
        with self.app.app_context():
            user = make_user()
            db.session.add(user)
            db.session.commit()
            db.session.add_all([
                make_bet(user.id, bet_amount=20.0, outcome="win"),
                make_bet(user.id, bet_amount=10.0, outcome="lose"),
                make_bet(user.id, bet_amount=5.0, outcome="pending"),
            ])
            db.session.commit()
            # win=+20, lose=-10, pending=0 → net=10
            self.assertAlmostEqual(user.net_profit_loss(), 10.0)

    def test_user_total_wins_losses(self):
        with self.app.app_context():
            user = make_user()
            db.session.add(user)
            db.session.commit()
            db.session.add_all([
                make_bet(user.id, outcome="win"),
                make_bet(user.id, outcome="win"),
                make_bet(user.id, outcome="lose"),
            ])
            db.session.commit()
            self.assertEqual(user.total_wins(), 2)
            self.assertEqual(user.total_losses(), 1)

    # Bet.profit_loss
    def test_bet_profit_loss_win(self):
        b = make_bet(1, bet_amount=50.0, outcome="win")
        self.assertAlmostEqual(b.profit_loss(), 50.0)

    def test_bet_profit_loss_lose(self):
        b = make_bet(1, bet_amount=50.0, outcome="lose")
        self.assertAlmostEqual(b.profit_loss(), -50.0)

    def test_bet_profit_loss_pending(self):
        b = make_bet(1, bet_amount=50.0, outcome="pending")
        self.assertAlmostEqual(b.profit_loss(), 0.0)

    def test_bet_profit_loss_push(self):
        b = make_bet(1, bet_amount=50.0, outcome="push")
        self.assertAlmostEqual(b.profit_loss(), 0.0)

    # Bet.expected_profit_for_win
    def test_expected_profit_positive_odds(self):
        b = make_bet(1, bet_amount=100.0, american_odds=200)
        self.assertAlmostEqual(b.expected_profit_for_win(), 200.0)

    def test_expected_profit_negative_odds(self):
        b = make_bet(1, bet_amount=110.0, american_odds=-110)
        self.assertAlmostEqual(b.expected_profit_for_win(), 100.0)

    def test_expected_profit_no_odds(self):
        b = make_bet(1, bet_amount=50.0, american_odds=None)
        self.assertAlmostEqual(b.expected_profit_for_win(), 50.0)

    # Bet.margin
    def test_bet_margin(self):
        b = make_bet(1, over_under_line=210.5, actual_total=215.0)
        self.assertAlmostEqual(b.margin, 4.5)

    def test_bet_margin_none_when_missing_data(self):
        self.assertIsNone(make_bet(1).margin)

    # Bet.is_player_prop / prop_display
    def test_bet_is_player_prop_true(self):
        b = make_bet(1, player_name="LeBron James", prop_type="player_points")
        self.assertTrue(b.is_player_prop)

    def test_bet_is_player_prop_false(self):
        self.assertFalse(make_bet(1).is_player_prop)

    def test_bet_prop_display(self):
        b = make_bet(
            1,
            player_name="LeBron James",
            prop_type="player_points",
            prop_line=25.5,
            bet_type="over",
        )
        self.assertIn("LeBron James", b.prop_display)
        self.assertIn("Over", b.prop_display)
        self.assertIn("25.5", b.prop_display)

    def test_bet_prop_display_none_when_not_prop(self):
        self.assertIsNone(make_bet(1).prop_display)

    # Bet.is_winning_bet / is_losing_bet
    def test_bet_is_winning_losing(self):
        win = make_bet(1, outcome="win")
        lose = make_bet(1, outcome="lose")
        self.assertTrue(win.is_winning_bet())
        self.assertFalse(win.is_losing_bet())
        self.assertTrue(lose.is_losing_bet())
        self.assertFalse(lose.is_winning_bet())

    # Bet.generate_parlay_id
    def test_generate_parlay_id_unique_and_correct_length(self):
        id1 = Bet.generate_parlay_id()
        id2 = Bet.generate_parlay_id()
        self.assertEqual(len(id1), 16)
        self.assertNotEqual(id1, id2)

    def test_bet_repr(self):
        b = make_bet(1)
        self.assertIn("Lakers", repr(b))
