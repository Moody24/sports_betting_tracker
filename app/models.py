from datetime import datetime

from flask_login import UserMixin
from werkzeug.security import check_password_hash, generate_password_hash

from . import db


class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(20), unique=True, nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.String(128), nullable=False)
    bets = db.relationship('Bet', backref='user', lazy=True)

    def __repr__(self):
        return f"<User {self.username}>"

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

    def total_bets(self):
        return db.session.query(Bet).filter_by(user_id=self.id).count()

    def total_amount_wagered(self):
        total = db.session.query(db.func.sum(Bet.bet_amount)).filter_by(user_id=self.id).scalar()
        return float(total or 0.0)

    def net_profit_loss(self):
        result = 0.0
        for bet in db.session.query(Bet).filter_by(user_id=self.id).all():
            result += bet.profit_loss()
        return round(result, 2)

    def total_wins(self):
        return db.session.query(Bet).filter_by(user_id=self.id, outcome='win').count()

    def total_losses(self):
        return db.session.query(Bet).filter_by(user_id=self.id, outcome='lose').count()


class Bet(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    team_a = db.Column(db.String(80), nullable=False)
    team_b = db.Column(db.String(80), nullable=False)
    match_date = db.Column(db.DateTime, nullable=False)
    bet_amount = db.Column(db.Float, nullable=False)
    outcome = db.Column(db.String(10), nullable=True, default='pending')  # win/lose/pending
    american_odds = db.Column(db.Integer, nullable=True)
    is_parlay = db.Column(db.Boolean, nullable=False, default=False)
    source = db.Column(db.String(40), nullable=False, default='manual')
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    def __repr__(self):
        return (
            f"<Bet {self.id} - {self.team_a} vs {self.team_b} - Amount: {self.bet_amount} "
            f"- Outcome: {self.outcome}>"
        )

    def is_winning_bet(self):
        return self.outcome == 'win'

    def is_losing_bet(self):
        return self.outcome == 'lose'

    def expected_profit_for_win(self):
        if self.american_odds is None:
            return float(self.bet_amount)

        stake = float(self.bet_amount)
        odds = int(self.american_odds)

        if odds > 0:
            return round(stake * odds / 100.0, 2)

        if odds < 0:
            return round(stake * 100.0 / abs(odds), 2)

        return 0.0

    def profit_loss(self):
        """Returns P/L excluding initial stake for winning days and negative stake for losses."""
        if self.outcome == 'win':
            return self.expected_profit_for_win()
        if self.outcome == 'lose':
            return -float(self.bet_amount)
        return 0.0
