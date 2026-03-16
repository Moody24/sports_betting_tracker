"""Odds conversion utilities for American / decimal / probability formats."""


def american_to_decimal(odds: int) -> float:
    """Convert American odds to decimal odds (includes stake).

    Treats 0 as even money (+100) → 2.0 decimal.
    """
    if odds > 0:
        return 1.0 + odds / 100.0
    if odds < 0:
        return 1.0 + 100.0 / abs(odds)
    return 2.0


def decimal_odds(american_odds: int) -> float:
    """Convert American odds to decimal odds (alias for american_to_decimal)."""
    return american_to_decimal(american_odds)


def implied_prob(american_odds: int) -> float:
    """Convert American odds to implied probability (0..1)."""
    if american_odds > 0:
        return 100.0 / (american_odds + 100.0)
    if american_odds < 0:
        return abs(american_odds) / (abs(american_odds) + 100.0)
    return 0.5


def american_from_decimal(decimal_value: float) -> int:
    """Convert decimal odds to American odds."""
    if decimal_value <= 1.0:
        return 0
    if decimal_value >= 2.0:
        return int(round((decimal_value - 1.0) * 100))
    return int(round(-100.0 / (decimal_value - 1.0)))
