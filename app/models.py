import json
import uuid
from datetime import datetime, timezone, date as date_type
from typing import Optional

from flask_login import UserMixin
from sqlalchemy import func, UniqueConstraint
from werkzeug.security import check_password_hash, generate_password_hash

from . import db
from .enums import BetType, Outcome


class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(20), unique=True, nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.String(128), nullable=False)
    starting_bankroll = db.Column(db.Float, nullable=True)
    bets = db.relationship("Bet", backref="user", lazy=True)

    def __repr__(self) -> str:
        return f"<User {self.username}>"

    def set_password(self, password: str) -> None:
        self.password_hash = generate_password_hash(password)

    def check_password(self, password: str) -> bool:
        return check_password_hash(self.password_hash, password)

    def total_bets(self) -> int:
        return db.session.query(Bet).filter_by(user_id=self.id).count()

    def total_amount_wagered(self) -> float:
        total = db.session.query(func.sum(Bet.bet_amount)).filter_by(user_id=self.id).scalar()
        return float(total or 0.0)

    def net_profit_loss(self) -> float:
        """Return net P/L, accounting for American odds and bonus multiplier."""
        bets = db.session.query(Bet).filter_by(user_id=self.id).all()
        return round(sum(b.profit_loss() for b in bets), 2)

    def total_wins(self) -> int:
        return db.session.query(Bet).filter_by(user_id=self.id, outcome=Outcome.WIN.value).count()

    def total_losses(self) -> int:
        return db.session.query(Bet).filter_by(user_id=self.id, outcome=Outcome.LOSE.value).count()


class Bet(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    team_a = db.Column(db.String(80), nullable=False)
    team_b = db.Column(db.String(80), nullable=False)
    match_date = db.Column(db.DateTime, nullable=False)
    bet_amount = db.Column(db.Float, nullable=False)
    outcome = db.Column(db.String(10), nullable=True, default=Outcome.PENDING.value)
    american_odds = db.Column(db.Integer, nullable=True)
    is_parlay = db.Column(db.Boolean, nullable=False, default=False)
    source = db.Column(db.String(40), nullable=False, default="manual")
    bet_type = db.Column(db.String(20), nullable=False, default=BetType.MONEYLINE.value)
    over_under_line = db.Column(db.Float, nullable=True)
    actual_total = db.Column(db.Float, nullable=True)
    external_game_id = db.Column(db.String(80), nullable=True)
    player_name = db.Column(db.String(100), nullable=True)
    prop_type = db.Column(db.String(40), nullable=True)
    prop_line = db.Column(db.Float, nullable=True)
    parlay_id = db.Column(db.String(40), nullable=True)
    picked_team = db.Column(db.String(80), nullable=True)
    bonus_multiplier = db.Column(db.Float, nullable=False, default=1.0)
    notes = db.Column(db.Text, nullable=True)
    created_at = db.Column(
        db.DateTime, nullable=False, default=lambda: datetime.now(timezone.utc)
    )

    def __repr__(self) -> str:
        return (
            f"<Bet {self.id} - {self.team_a} vs {self.team_b} "
            f"- Amount: {self.bet_amount} - Outcome: {self.outcome}>"
        )

    def is_winning_bet(self) -> bool:
        return self.outcome == Outcome.WIN.value

    def is_losing_bet(self) -> bool:
        return self.outcome == Outcome.LOSE.value

    def expected_profit_for_win(self) -> float:
        stake = float(self.bet_amount)
        multiplier = float(self.bonus_multiplier or 1.0)

        if self.american_odds is None:
            return round(stake * multiplier, 2)

        odds = int(self.american_odds)
        if odds > 0:
            profit = round(stake * odds / 100.0, 2)
        elif odds < 0:
            profit = round(stake * 100.0 / abs(odds), 2)
        else:
            profit = 0.0

        return round(profit * multiplier, 2)

    def profit_loss(self) -> float:
        if self.outcome == Outcome.WIN.value:
            return self.expected_profit_for_win()
        if self.outcome == Outcome.LOSE.value:
            return -float(self.bet_amount)
        return 0.0

    @property
    def margin(self) -> Optional[float]:
        if self.actual_total is None:
            return None
        if self.is_player_prop and self.prop_line is not None:
            return round(self.actual_total - self.prop_line, 1)
        if self.over_under_line is not None:
            return round(self.actual_total - self.over_under_line, 1)
        return None

    @property
    def is_player_prop(self) -> bool:
        return bool(self.player_name and self.prop_type)

    @property
    def prop_display(self) -> Optional[str]:
        """Human-readable prop description, e.g. 'LeBron James Over 25.5 Points'."""
        if not self.is_player_prop:
            return None
        label = self.prop_type.replace("player_", "").replace("_", " ").title()
        direction = self.bet_type.capitalize() if self.bet_type in (
            BetType.OVER.value, BetType.UNDER.value
        ) else ""
        return f"{self.player_name} {direction} {self.prop_line} {label}".strip()

    @staticmethod
    def generate_parlay_id() -> str:
        return uuid.uuid4().hex[:16]


class GameSnapshot(db.Model):
    """Persistent archive of a game's odds/props captured at first view.

    Odds and props are locked at the time the game is first seen so that
    completed games always display the lines that were available at the time,
    regardless of what the live API returns later.
    """

    id = db.Column(db.Integer, primary_key=True)
    espn_id = db.Column(db.String(80), nullable=False, index=True)
    game_date = db.Column(db.Date, nullable=False)
    home_team = db.Column(db.String(100), nullable=False)
    away_team = db.Column(db.String(100), nullable=False)
    home_logo = db.Column(db.String(300), nullable=True)
    away_logo = db.Column(db.String(300), nullable=True)
    home_score = db.Column(db.Integer, nullable=True)
    away_score = db.Column(db.Integer, nullable=True)
    status = db.Column(db.String(40), nullable=False, default="STATUS_SCHEDULED")
    # Odds locked at first snapshot — never overwritten
    over_under_line = db.Column(db.Float, nullable=True)
    moneyline_home = db.Column(db.Integer, nullable=True)
    moneyline_away = db.Column(db.Integer, nullable=True)
    # Props JSON locked when first fetched via the props endpoint
    props_json = db.Column(db.Text, nullable=True)
    snapshot_time = db.Column(
        db.DateTime, nullable=False, default=lambda: datetime.now(timezone.utc)
    )
    is_final = db.Column(db.Boolean, nullable=False, default=False)

    def __repr__(self) -> str:
        return f"<GameSnapshot {self.espn_id} {self.game_date}>"

    @property
    def props(self) -> dict:
        """Deserialise stored props JSON; return empty dict if none."""
        if self.props_json:
            try:
                return json.loads(self.props_json)
            except (ValueError, TypeError):
                return {}
        return {}

    @property
    def total_score(self) -> int:
        return (self.home_score or 0) + (self.away_score or 0)


class PlayerGameLog(db.Model):
    """Cached player game log fetched from NBA API.

    Only stores rows for players on tonight's slate.  Rows older than
    ``cache_expires`` are pruned by the scheduler.
    """

    id = db.Column(db.Integer, primary_key=True)
    player_id = db.Column(db.String(20), nullable=False, index=True)
    player_name = db.Column(db.String(120), nullable=False)
    team_abbr = db.Column(db.String(10), nullable=True)
    game_date = db.Column(db.Date, nullable=False)
    matchup = db.Column(db.String(40), nullable=True)
    minutes = db.Column(db.Float, nullable=True)
    pts = db.Column(db.Float, default=0)
    reb = db.Column(db.Float, default=0)
    ast = db.Column(db.Float, default=0)
    stl = db.Column(db.Float, default=0)
    blk = db.Column(db.Float, default=0)
    tov = db.Column(db.Float, default=0)
    fgm = db.Column(db.Float, default=0)
    fga = db.Column(db.Float, default=0)
    ftm = db.Column(db.Float, default=0)
    fta = db.Column(db.Float, default=0)
    fg3m = db.Column(db.Float, default=0)
    fg3a = db.Column(db.Float, default=0)
    plus_minus = db.Column(db.Float, default=0)
    home_away = db.Column(db.String(4), nullable=True)
    win_loss = db.Column(db.String(1), nullable=True)
    context_flags = db.Column(db.Text, nullable=True)
    cache_expires = db.Column(db.DateTime, nullable=True)
    fetched_at = db.Column(
        db.DateTime, nullable=False, default=lambda: datetime.now(timezone.utc)
    )

    __table_args__ = (
        UniqueConstraint('player_id', 'game_date', name='uq_player_game_date'),
    )

    def __repr__(self) -> str:
        return f"<PlayerGameLog {self.player_name} {self.game_date}>"


class TeamDefenseSnapshot(db.Model):
    """Daily snapshot of a team's defensive profile."""

    id = db.Column(db.Integer, primary_key=True)
    team_id = db.Column(db.String(20), nullable=False)
    team_name = db.Column(db.String(100), nullable=False)
    team_abbr = db.Column(db.String(10), nullable=True)
    snapshot_date = db.Column(db.Date, nullable=False)
    opp_pts_pg = db.Column(db.Float, nullable=True)
    opp_reb_pg = db.Column(db.Float, nullable=True)
    opp_ast_pg = db.Column(db.Float, nullable=True)
    opp_3pm_pg = db.Column(db.Float, nullable=True)
    opp_stl_pg = db.Column(db.Float, nullable=True)
    opp_blk_pg = db.Column(db.Float, nullable=True)
    opp_tov_pg = db.Column(db.Float, nullable=True)
    pace = db.Column(db.Float, nullable=True)
    def_rating = db.Column(db.Float, nullable=True)
    opp_pts_allowed_pg = db.Column(db.Float, nullable=True)
    opp_pts_allowed_sg = db.Column(db.Float, nullable=True)
    opp_pts_allowed_sf = db.Column(db.Float, nullable=True)
    opp_pts_allowed_pf = db.Column(db.Float, nullable=True)
    opp_pts_allowed_c = db.Column(db.Float, nullable=True)
    fetched_at = db.Column(
        db.DateTime, nullable=False, default=lambda: datetime.now(timezone.utc)
    )

    __table_args__ = (
        UniqueConstraint('team_id', 'snapshot_date', name='uq_team_defense_date'),
    )

    def __repr__(self) -> str:
        return f"<TeamDefenseSnapshot {self.team_name} {self.snapshot_date}>"


class InjuryReport(db.Model):
    """Current injury designations for NBA players."""

    id = db.Column(db.Integer, primary_key=True)
    player_name = db.Column(db.String(120), nullable=False)
    team = db.Column(db.String(100), nullable=True)
    status = db.Column(db.String(20), nullable=False)
    detail = db.Column(db.String(300), nullable=True)
    date_reported = db.Column(db.Date, nullable=False)
    fetched_at = db.Column(
        db.DateTime, nullable=False, default=lambda: datetime.now(timezone.utc)
    )

    def __repr__(self) -> str:
        return f"<InjuryReport {self.player_name} {self.status}>"


class PickContext(db.Model):
    """Snapshot of analysis context at bet placement time for Model 2 training."""

    id = db.Column(db.Integer, primary_key=True)
    bet_id = db.Column(
        db.Integer, db.ForeignKey('bet.id'), nullable=False, unique=True
    )
    context_json = db.Column(db.Text, nullable=False)
    projected_stat = db.Column(db.Float, nullable=True)
    projected_edge = db.Column(db.Float, nullable=True)
    confidence_tier = db.Column(db.String(20), nullable=True)
    created_at = db.Column(
        db.DateTime, nullable=False, default=lambda: datetime.now(timezone.utc)
    )

    bet = db.relationship('Bet', backref=db.backref('pick_context', uselist=False))

    def __repr__(self) -> str:
        return f"<PickContext bet_id={self.bet_id}>"

    @property
    def context(self) -> dict:
        if self.context_json:
            try:
                return json.loads(self.context_json)
            except (ValueError, TypeError):
                return {}
        return {}


class ModelMetadata(db.Model):
    """Tracks trained ML model versions."""

    id = db.Column(db.Integer, primary_key=True)
    model_name = db.Column(db.String(80), nullable=False)
    model_type = db.Column(db.String(40), nullable=False)
    version = db.Column(db.String(40), nullable=False)
    file_path = db.Column(db.String(300), nullable=False)
    training_date = db.Column(db.DateTime, nullable=False)
    training_samples = db.Column(db.Integer, nullable=True)
    val_mae = db.Column(db.Float, nullable=True)
    val_accuracy = db.Column(db.Float, nullable=True)
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    metadata_json = db.Column(db.Text, nullable=True)
    created_at = db.Column(
        db.DateTime, nullable=False, default=lambda: datetime.now(timezone.utc)
    )

    def __repr__(self) -> str:
        return f"<ModelMetadata {self.model_name} v{self.version}>"


class JobLog(db.Model):
    """Audit log for scheduled background job executions."""

    id = db.Column(db.Integer, primary_key=True)
    job_name = db.Column(db.String(80), nullable=False)
    started_at = db.Column(db.DateTime, nullable=False)
    finished_at = db.Column(db.DateTime, nullable=True)
    status = db.Column(db.String(20), nullable=False, default='running')
    message = db.Column(db.Text, nullable=True)

    def __repr__(self) -> str:
        return f"<JobLog {self.job_name} {self.status}>"
