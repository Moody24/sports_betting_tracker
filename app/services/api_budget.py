"""Request-budget tracking for The Odds API.

The Odds API returns ``x-requests-remaining`` / ``x-requests-used`` headers
on every response.  ``APIBudgetManager`` records them and refuses
*non-critical* calls once the remaining budget drops below a floor, so
discretionary jobs (prop scans) can never starve critical ones (bet
grading, closing-line capture).
"""

import logging
import os
import threading
from typing import Mapping, Optional

import requests

logger = logging.getLogger(__name__)


class BudgetExhaustedError(requests.RequestException):
    """Raised when a non-critical call is refused to preserve quota.

    Subclasses ``requests.RequestException`` so existing call sites that
    catch that degrade gracefully (empty results) without modification.
    """


class APIBudgetManager:
    def __init__(self, floor: Optional[int] = None):
        self._remaining: Optional[float] = None
        self._floor = floor if floor is not None else int(
            os.getenv('ODDS_API_BUDGET_FLOOR', '25')
        )
        self._lock = threading.Lock()

    @property
    def remaining(self) -> Optional[float]:
        return self._remaining

    def record_headers(self, headers: Mapping) -> None:
        lowered = {str(k).lower(): v for k, v in headers.items()}
        raw = lowered.get('x-requests-remaining')
        if raw is None:
            return
        try:
            value = float(raw)
        except (TypeError, ValueError):
            return
        with self._lock:
            self._remaining = value
        if value < self._floor:
            logger.warning(
                "Odds API budget low: %.0f remaining (floor %d)",
                value, self._floor,
            )

    def can_spend(self, critical: bool = False) -> bool:
        if critical or self._remaining is None:
            return True
        return self._remaining >= self._floor

    def budgeted_get(self, url, params=None, timeout=10, critical=False):
        """``requests.get`` wrapper that enforces and records the budget."""
        if not self.can_spend(critical):
            raise BudgetExhaustedError(
                f"Odds API budget below floor ({self._remaining} < {self._floor}); "
                "non-critical call refused"
            )
        resp = requests.get(url, params=params, timeout=timeout)
        self.record_headers(resp.headers)
        return resp


ODDS_BUDGET = APIBudgetManager()
"""Process-wide singleton for all The Odds API calls."""
