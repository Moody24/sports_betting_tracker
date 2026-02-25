import uuid
from datetime import datetime, timezone

from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash

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
    outcome = db.Column(db.String(10), nullable=True, default='pending')
    american_odds = db.Column(db.Integer, nullable=True)
    is_parlay = db.Column(db.Boolean, nullable=False, default=False)
    source = db.Column(db.String(40), nullable=False, default='manual')
    bet_type = db.Column(db.String(20), nullable=False, default='moneyline')
    over_under_line = db.Column(db.Float, nullable=True)
    actual_total = db.Column(db.Float, nullable=True)
    external_game_id = db.Column(db.String(80), nullable=True)
    player_name = db.Column(db.String(100), nullable=True)
    prop_type = db.Column(db.String(40), nullable=True)
    prop_line = db.Column(db.Float, nullable=True)
    parlay_id = db.Column(db.String(40), nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))

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
        if self.outcome == "win":
            return float(self.bet_amount)
        if self.outcome == "lose":
            return -float(self.bet_amount)
        return 0.0

    @property
    def margin(self):
        if self.over_under_line is not None and self.actual_total is not None:
            return round(self.actual_total - self.over_under_line, 1)
        return None

    @property
    def is_player_prop(self):
        return bool(self.player_name and self.prop_type)

    @property
    def prop_display(self):
        """Human-readable prop description, e.g. 'LeBron James Over 25.5 Points'."""
        if not self.is_player_prop:
            return None
        label = self.prop_type.replace('player_', '').replace('_', ' ').title()
        direction = self.bet_type.capitalize() if self.bet_type in ('over', 'under') else ''
        return f"{self.player_name} {direction} {self.prop_line} {label}".strip()

    @staticmethod
    def generate_parlay_id():
        return uuid.uuid4().hex[:16]
