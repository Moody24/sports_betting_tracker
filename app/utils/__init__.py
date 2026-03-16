"""Shared utility helpers for the Edge Tracker application."""

import logging
import os

logger = logging.getLogger(__name__)


def safe_float(value, default: float = 0.0) -> float:
    """Convert value to float, stripping leading '+' and whitespace.

    Handles both plain numeric strings ("3.5") and signed strings ("+3.5")
    as emitted by ESPN and other sports data APIs.
    """
    try:
        return float(str(value).replace("+", "").strip())
    except (ValueError, TypeError):
        return default


def env_float(name: str, default: float) -> float:
    """Read a float from an environment variable, returning *default* on failure."""
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        logger.warning("Invalid %s=%r; using default %.3f", name, raw, default)
        return default
