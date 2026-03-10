"""
String-valued enumerations used across the app.

Using (str, enum.Enum) so that values compare equal to plain strings,
making them safe to use in SQLAlchemy queries, WTForms choices, and
Jinja2 templates without any conversion.  The __str__ override ensures
str(Outcome.WIN) == 'win' on Python < 3.11.
"""
import enum


class _StrEnum(str, enum.Enum):
    """Base for string enums compatible with Python 3.9+."""

    def __str__(self) -> str:  # pragma: no cover
        return self.value


class Outcome(_StrEnum):
    WIN = "win"
    LOSE = "lose"
    PENDING = "pending"
    PUSH = "push"


class BetType(_StrEnum):
    MONEYLINE = "moneyline"
    OVER = "over"
    UNDER = "under"


class BetSource(_StrEnum):
    MANUAL = "manual"
    NBA_PROPS = "nba_props"
    AUTO_GENERATED = "auto_generated"


class PostmortemReason(_StrEnum):
    """Structured reason codes for bet postmortem diagnosis.

    Rules engine assigns these after settlement by comparing pregame
    expectations (from PickContext) to postgame reality (PlayerGameLog).
    At most one primary + two secondary codes are stored per settled leg.
    """

    # Volume / usage drivers
    MINUTES_MISS = "minutes_miss"          # actual minutes materially exceeded/fell short of expected
    ROLE_CHANGE = "role_change"             # large unexpected shift in role (minutes were previously stable)
    VOLUME_SPIKE = "volume_spike"           # attempts (FGA / FG3A) well above historical expectation
    VOLUME_DROP = "volume_drop"             # attempts well below historical expectation

    # Shooting efficiency drivers
    EFFICIENCY_SPIKE = "efficiency_spike"   # rate (stat/attempt) was unusually high vs expectation
    EFFICIENCY_DROP = "efficiency_drop"     # rate (stat/attempt) was unusually low vs expectation

    # Game-context drivers
    PACE_MISS = "pace_miss"                 # game pace deviated significantly from expected
    MATCHUP_MISS = "matchup_miss"           # opponent defence performed differently than modelled
    OT_VARIANCE = "ot_variance"             # game went to overtime, inflating counting stats
    BLOWOUT_DISTORTION = "blowout_distortion"  # blowout caused bench time / garbage-time distortion

    # Information drivers
    INJURY_CONTEXT_MISS = "injury_context_miss"        # teammate injury changed opportunity
    TEAMMATE_AVAILABILITY_SHIFT = "teammate_availability_shift"  # roster change altered usage

    # Market / model drivers
    LINE_VALUE_MISS = "line_value_miss"               # line had insufficient edge at placement
    INSUFFICIENT_EDGE = "insufficient_edge"            # model projected negative edge
    MARKET_MOVED_AGAINST_US = "market_moved_against_us"  # line moved unfavourably after placement
    PROJECTION_MODEL_MISS = "projection_model_miss"   # projection was directionally wrong, no clear delta driver

    # Catch-alls
    HIGH_VARIANCE_EVENT = "high_variance_event"       # large miss consistent with high-variance player
    NORMAL_VARIANCE = "normal_variance"                # within expected statistical noise band
    UNKNOWN = "unknown"                                # insufficient data to diagnose
