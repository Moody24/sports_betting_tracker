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
