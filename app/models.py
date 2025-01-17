from datetime import datetime
from flask_login import UserMixin
from . import db, bcrypt  # ✅ Use relative imports to avoid circular import issues

class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(20), unique=True, nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.String(128), nullable=False)
    bets = db.relationship('Bet', backref='user', lazy=True)

    def __repr__(self):
        return f"<User {self.username}>"

    def set_password(self, password):
        """Hashes and sets the user's password."""
        self.password_hash = bcrypt.generate_password_hash(password).decode('utf-8')

    def check_password(self, password):
        """Checks if the provided password matches the hashed password."""
        return bcrypt.check_password_hash(self.password_hash, password)

    def total_wins(self):
        """Returns the total number of winning bets for the user."""
        return db.session.query(Bet).filter_by(user_id=self.id, outcome="win").count()

    def total_losses(self):
        """Returns the total number of losing bets for the user."""
        return db.session.query(Bet).filter_by(user_id=self.id, outcome="lose").count()

class Bet(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    team_a = db.Column(db.String(80), nullable=False)
    team_b = db.Column(db.String(80), nullable=False)
    match_date = db.Column(db.DateTime, nullable=False)
    bet_amount = db.Column(db.Float, nullable=False)
    outcome = db.Column(db.String(10), nullable=True, default='pending')  # win/lose/pending
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    def __repr__(self):
        return f"<Bet {self.id} - {self.team_a} vs {self.team_b} - Amount: {self.bet_amount} - Outcome: {self.outcome}>"

    def is_winning_bet(self):
        """Checks if the bet was a winning bet."""
        return self.outcome == "win"

    def is_losing_bet(self):
        """Checks if the bet was a losing bet."""
        return self.outcome == "lose"

