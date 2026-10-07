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

from app.services.provider_access import request_with_retry

logger = logging.getLogger(__name__)


class BudgetExhaustedError(requests.RequestException):
    """Raised when a non-critical call is refused to preserve quota.

    Subclasses ``requests.RequestException`` so existing call sites that
    catch that degrade gracefully (empty results) without modification.
    """


class APIBudgetManager:
    def __init__(self, floor: Optional[int] = None):
        self._remaining: Optional[float] = None
        if floor is not None:
            self._floor = floor
        else:
            try:
                self._floor = int(os.getenv('ODDS_API_BUDGET_FLOOR', '25'))
            except (TypeError, ValueError):
                logger.warning(
                    "ODDS_API_BUDGET_FLOOR env var invalid or non-numeric; using default floor 25"
                )
                self._floor = 25
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
        def check_budget():
            if not self.can_spend(critical):
                raise BudgetExhaustedError(
                    f"Odds API budget below floor ({self._remaining} < {self._floor}); "
                    "non-critical call refused"
                )

        return request_with_retry(
            requests.get, url, provider='The Odds API', params=params, timeout=timeout,
            before_attempt=check_budget,
            on_response=lambda response: self.record_headers(response.headers),
        )


ODDS_BUDGET = APIBudgetManager()
"""Process-wide singleton for all The Odds API calls."""
