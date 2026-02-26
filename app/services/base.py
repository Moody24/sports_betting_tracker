"""Abstract base class for sport-specific service implementations.

To add a new sport (e.g. NFL, MLB), subclass ``SportService`` and implement
every abstract method.  Then register the instance in the ``SPORT_REGISTRY``
dict at the bottom of this file (or in ``__init__.py``).
"""

from abc import ABC, abstractmethod
from typing import Optional


class SportService(ABC):
    """Interface that every sport backend must implement."""

    @property
    @abstractmethod
    def sport_key(self) -> str:
        """Short identifier, e.g. 'nba', 'nfl'."""

    @property
    @abstractmethod
    def display_name(self) -> str:
        """Human-readable name, e.g. 'NBA', 'NFL'."""

    # ── Live scores / schedule ────────────────────────────────────────

    @abstractmethod
    def fetch_scoreboard(self, date_str: Optional[str] = None) -> list[dict]:
        """Return today's (or a specific date's) games with scores."""

    @abstractmethod
    def fetch_boxscore(self, game_id: str) -> dict:
        """Return final player/team stats for a completed game."""

    # ── Odds ──────────────────────────────────────────────────────────

    @abstractmethod
    def fetch_odds_combined(self) -> tuple[dict, dict]:
        """Return (totals_map, h2h_map) from odds provider."""

    @abstractmethod
    def fetch_odds_events(self) -> dict:
        """Return odds-provider event IDs mapped by matchup key."""

    @abstractmethod
    def fetch_upcoming_games(self) -> list[dict]:
        """Return tomorrow's games with pre-game lines."""

    @abstractmethod
    def fetch_player_props(self, event_id: str) -> dict:
        """Return player prop lines for a given event."""

    # ── Merged views ──────────────────────────────────────────────────

    @abstractmethod
    def get_todays_games(self) -> list[dict]:
        """Merge live scores with odds into a unified game list."""

    @abstractmethod
    def get_player_props_for_game(self, game_id: str, games: Optional[list[dict]] = None) -> dict:
        """Return player props for a game identified by its game ID."""

    # ── Bet resolution ────────────────────────────────────────────────

    @abstractmethod
    def resolve_pending_bets(self, pending_bets: list) -> list[tuple]:
        """Grade pending bets against final results.

        Returns a list of ``(bet, new_outcome, actual_value)`` tuples.
        """

    # ── Prop market definitions ───────────────────────────────────────

    @abstractmethod
    def get_prop_markets(self) -> list[str]:
        """Return the list of supported player-prop market keys."""


# ── Registry ──────────────────────────────────────────────────────────

SPORT_REGISTRY: dict[str, SportService] = {}
"""Map of sport_key → SportService singleton.  Populated at import time by
each concrete service module (see ``nba_service.py``)."""


def get_sport_service(sport_key: str) -> SportService:
    """Look up a registered sport service by key.

    Raises ``KeyError`` if the sport is not registered.
    """
    return SPORT_REGISTRY[sport_key]
