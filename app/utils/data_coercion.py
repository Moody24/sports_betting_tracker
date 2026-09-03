"""Consistent coercion for nullable values from sports-data providers."""

import math


def safe_float(value, default: float = 0.0) -> float:
    """Coerce a value to float, mapping missing and NaN values to default."""
    if value is None:
        return default
    try:
        as_float = float(value)
    except (TypeError, ValueError):
        return default
    return default if math.isnan(as_float) else as_float


def safe_str(value) -> str:
    """Coerce a value to text, mapping missing and NaN values to an empty string."""
    if value is None:
        return ''
    if isinstance(value, float) and math.isnan(value):
        return ''
    return str(value)


def normalize_player_id(value) -> str:
    """Normalize integer-like provider player IDs to a canonical string."""
    if value is None:
        return ''
    if isinstance(value, float):
        if math.isnan(value):
            return ''
        if value.is_integer():
            return str(int(value))
    return str(value)
