import json
import uuid
from datetime import datetime, timezone, date as date_type
from typing import Optional

from flask_login import UserMixin
from sqlalchemy import func, UniqueConstraint
from werkzeug.security import check_password_hash, generate_password_hash

from . import db
from .enums import BetType, Outcome


def _american_to_decimal(odds: int) -> float:
    """Convert American odds to decimal odds (includes stake)."""
    if odds > 0:
        return 1.0 + odds / 100.0
    if odds < 0:
        return 1.0 + 100.0 / abs(odds)
    return 1.0  # 0 is not a valid real-world line; treat as even


def compute_bets_net_pl(bets: list) -> float:
    """Parlay-aware net P/L across a list of Bet objects.

    Parlay legs sharing a parlay_id are collapsed into a single group and
    their combined profit/loss is calculated with the correct multiplicative-
    odds formula.  Non-parlay bets use the existing profit_loss() method.
    """
    parlay_groups: dict = {}
    total = 0.0
    for b in bets:
        if b.is_parlay and b.parlay_id:
            parlay_groups.setdefault(b.parlay_id, []).append(b)
        else:
            total += b.profit_loss()
    for legs in parlay_groups.values():
        total += Bet.parlay_profit_loss(legs)
    return round(total, 2)


class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(20), unique=True, nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.String(512), nullable=False)
    starting_bankroll = db.Column(db.Float, nullable=True)
    unit_size = db.Column(db.Float, nullable=True)
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
        """Return net P/L using parlay-aware combined-odds calculation.

        Parlay legs are grouped and computed as a single event so that:
        - A losing parlay deducts the stake once (not once per leg).
        - A winning parlay profit reflects the correct multiplicative payout.
        """
        bets = (
            db.session.query(Bet)
            .filter_by(user_id=self.id)
            .filter(Bet.outcome.in_([Outcome.WIN.value, Outcome.LOSE.value, Outcome.PUSH.value]))
            .all()
        )
        return compute_bets_net_pl(bets)

    def total_wins(self) -> int:
        return db.session.query(Bet).filter_by(user_id=self.id, outcome=Outcome.WIN.value).count()

    def total_losses(self) -> int:
        return db.session.query(Bet).filter_by(user_id=self.id, outcome=Outcome.LOSE.value).count()


class Bet(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False, index=True)
    team_a = db.Column(db.String(80), nullable=False)
    team_b = db.Column(db.String(80), nullable=False)
    match_date = db.Column(db.DateTime, nullable=False, index=True)
    bet_amount = db.Column(db.Float, nullable=False)
    units = db.Column(db.Float, nullable=True)
    outcome = db.Column(db.String(10), nullable=True, default=Outcome.PENDING.value, index=True)
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
    parlay_id = db.Column(db.String(40), nullable=True, index=True)
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

        # Default to -110 (standard vig) when no odds stored.
        # Previously returned the stake itself, which inflated net P/L.
        odds = int(self.american_odds) if self.american_odds is not None else -110
        if odds > 0:
            profit = round(stake * odds / 100.0, 2)
        elif odds < 0:
            profit = round(stake * 100.0 / abs(odds), 2)
        else:
            profit = 0.0

        return round(profit * multiplier, 2)

    def profit_loss(self) -> float:
        """P/L for a single straight bet.

        For parlay legs, call Bet.parlay_profit_loss(legs) on the whole group
        instead of summing this method per-leg — per-leg results are incorrect
        because they ignore combined odds and double-count the stake on losses.
        """
        if self.outcome == Outcome.WIN.value:
            return self.expected_profit_for_win()
        if self.outcome == Outcome.LOSE.value:
            return -float(self.bet_amount)
        return 0.0

    @staticmethod
    def parlay_profit_loss(legs: list) -> float:
        """Correct combined P/L for a complete set of parlay legs.

        Rules:
        - Any LOSE leg   → stake lost once (not once per leg)
        - Any PENDING    → unsettled, returns 0
        - All WIN/PUSH   → stake × (∏ decimal_odds of WIN legs) − stake
        - All PUSH       → stake returned (profit = 0)

        The stake used is legs[0].bet_amount — all legs share the same stake.
        """
        if not legs:
            return 0.0

        stake = float(legs[0].bet_amount)
        multiplier = float(legs[0].bonus_multiplier or 1.0)
        outcomes = [l.outcome for l in legs]

        if any(o == Outcome.LOSE.value for o in outcomes):
            return -stake
        if any(o == Outcome.PENDING.value for o in outcomes):
            return 0.0

        # All legs are WIN or PUSH — multiply decimal odds of WIN legs only.
        combined_decimal = 1.0
        for leg in legs:
            if leg.outcome == Outcome.WIN.value:
                combined_decimal *= _american_to_decimal(leg.american_odds or -110)

        if combined_decimal == 1.0:
            return 0.0  # every leg pushed — stake returned

        profit = round(stake * combined_decimal - stake, 2)
        return round(profit * multiplier, 2)

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
        label = self._prop_stat_label(self.prop_type)
        direction = self.bet_type.capitalize() if self.bet_type in (
            BetType.OVER.value, BetType.UNDER.value
        ) else ""
        return f"{self.player_name} {direction} {self.prop_line} {label}".strip()

    @property
    def primary_display_name(self) -> str:
        if self.is_player_prop:
            return (self.player_name or 'Player prop').strip()
        if self.bet_type == BetType.MONEYLINE.value:
            return (self.picked_team or self.team_a or self.team_b or 'Moneyline').strip()
        if self.bet_type == 'spread':
            return (self.picked_team or self.team_a or self.team_b or 'Spread').strip()
        if self.bet_type in (BetType.OVER.value, BetType.UNDER.value):
            return f"{self.team_a} vs {self.team_b}".strip()
        return (self.display_label or 'Bet').strip()

    @property
    def market_display(self) -> str:
        if self.is_player_prop:
            return self._prop_stat_label(self.prop_type)
        labels = {
            BetType.MONEYLINE.value: 'ML',
            BetType.OVER.value: 'Total',
            BetType.UNDER.value: 'Total',
            'spread': 'Spread',
        }
        return labels.get((self.bet_type or '').lower(), (self.bet_type or 'Bet').upper())

    @property
    def selection_display(self) -> str:
        bet_type = (self.bet_type or '').lower()
        if self.is_player_prop:
            direction = 'Over' if bet_type == BetType.OVER.value else 'Under' if bet_type == BetType.UNDER.value else ''
            line = '?' if self.prop_line is None else f"{self.prop_line:g}"
            return f"{direction} {line}".strip()

        if bet_type == BetType.MONEYLINE.value:
            team = self.picked_team or self.team_a or self.team_b
            return f"{team} ML" if team else 'Moneyline'

        if bet_type in (BetType.OVER.value, BetType.UNDER.value):
            line = '?' if self.over_under_line is None else f"{self.over_under_line:g}"
            return f"{'Over' if bet_type == BetType.OVER.value else 'Under'} {line}"

        if bet_type == 'spread':
            team = self.picked_team or self.team_a or self.team_b or 'Spread'
            spread_line = getattr(self, 'spread_line', None)
            if spread_line is not None:
                return f"{team} {spread_line}"
            return team

        return self.display_label

    @property
    def odds_display(self) -> str:
        if self.american_odds is None:
            return '—'
        return f"{int(self.american_odds):+d}"

    @property
    def matchup_display(self) -> str:
        return f"{self.team_a} vs {self.team_b}"

    @property
    def bet_kind_display(self) -> str:
        if self.is_parlay:
            return 'Parlay Leg'
        if self.is_player_prop:
            return 'Prop'
        return 'Straight'

    @property
    def live_trackable(self) -> bool:
        supported = {
            'player_points',
            'player_rebounds',
            'player_assists',
            'player_points_rebounds_assists',
            'player_threes',
            'player_steals',
            'player_blocks',
        }
        return bool(self.is_player_prop and self.external_game_id and (self.prop_type or '') in supported)

    @staticmethod
    def _prop_stat_label(prop_type: Optional[str]) -> str:
        if not prop_type:
            return "Stat"
        labels = {
            "player_points": "PTS",
            "player_rebounds": "REB",
            "player_assists": "AST",
            "player_threes": "3PM",
            "player_blocks": "BLK",
            "player_steals": "STL",
            "player_points_rebounds_assists": "PTS+REB+AST",
            "player_points_rebounds": "PTS+REB",
            "player_points_assists": "PTS+AST",
            "player_rebounds_assists": "REB+AST",
        }
        return labels.get(prop_type, prop_type.replace("player_", "").replace("_", " ").upper())

    @property
    def display_label(self) -> str:
        bet_type = (self.bet_type or "").lower()
        direction = "Over" if bet_type == BetType.OVER.value else "Under" if bet_type == BetType.UNDER.value else ""
        prefix = ""
        if self.is_parlay and self.parlay_id:
            num_legs = getattr(self, "_parlay_legs_count", None)
            if num_legs is None:
                num_legs = Bet.query.filter_by(user_id=self.user_id, parlay_id=self.parlay_id).count()
            prefix = f"Parlay — {num_legs} legs · "

        if self.is_player_prop:
            line = self.prop_line if self.prop_line is not None else "?"
            return f"{prefix}Prop — {self.player_name} {direction} {line} {self._prop_stat_label(self.prop_type)}".strip()

        if bet_type in (BetType.OVER.value, BetType.UNDER.value):
            line = self.over_under_line if self.over_under_line is not None else "?"
            return f"{prefix}Total — {direction} {line}".strip()

        if bet_type == BetType.MONEYLINE.value:
            picked = self.picked_team or "(missing winner)"
            return f"{prefix}Moneyline — {picked}".strip()

        if bet_type == "spread":
            spread_line = getattr(self, "spread_line", None)
            team = self.picked_team or "(missing team)"
            if spread_line is not None:
                return f"{prefix}Spread — {team} {spread_line}".strip()
            return f"{prefix}Spread — {team}".strip()

        return f"{prefix}{bet_type.title() if bet_type else 'Bet'}".strip()

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
        db.Integer, db.ForeignKey('bet.id', ondelete='CASCADE'), nullable=False, unique=True
    )
    context_json = db.Column(db.Text, nullable=False)
    projected_stat = db.Column(db.Float, nullable=True)
    projected_edge = db.Column(db.Float, nullable=True)
    confidence_tier = db.Column(db.String(20), nullable=True)
    created_at = db.Column(
        db.DateTime, nullable=False, default=lambda: datetime.now(timezone.utc)
    )

    bet = db.relationship('Bet', backref=db.backref('pick_context', uselist=False, cascade='all, delete-orphan', single_parent=True))

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
