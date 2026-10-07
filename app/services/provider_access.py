"""Source admission, secret-free status, and bounded read-only HTTP retries."""

import logging
import math
import os
import random
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

from flask import current_app, has_app_context
import requests

logger = logging.getLogger(__name__)
MAX_ATTEMPTS = 3
MAX_TIMEOUT_SECONDS = 15
MAX_BACKOFF_SECONDS = 10


def _setting(name):
    if has_app_context() and name in current_app.config:
        return current_app.config[name]
    return os.getenv(name, '')


def provider_access_status():
    """Describe configured access without exposing credentials or rights text."""
    testing = has_app_context() and current_app.config.get('TESTING') is True
    basis = bool(str(_setting('ESPN_RIGHTS_BASIS') or '').strip())
    approved = str(_setting('ESPN_ACCESS_APPROVED')).strip().lower() == 'true'
    enabled = testing or (approved and basis)
    nba_basis = bool(str(_setting('NBA_RIGHTS_BASIS') or '').strip())
    nba_approved = str(_setting('NBA_ACCESS_APPROVED')).strip().lower() == 'true'
    nba_enabled = testing or (nba_approved and nba_basis)
    odds_configured = bool(str(_setting('ODDS_API_KEY') or '').strip())
    return {
        'espn': {
            'enabled': enabled,
            'state': 'testing' if testing else ('approved' if enabled else 'blocked'),
            'label': 'ESPN',
            'reason': ('Test fixture context' if testing else (
                'Operator rights basis configured' if enabled else 'Source permission required'
            )),
            'rights_basis_configured': basis,
        },
        'odds_api': {
            'enabled': odds_configured,
            'state': 'configured' if odds_configured else 'unconfigured',
            'label': 'The Odds API',
            'reason': 'API key configured' if odds_configured else 'API key required',
        },
        'nba_stats': {
            'enabled': nba_enabled,
            'state': 'testing' if testing else ('approved' if nba_enabled else 'blocked'),
            'label': 'NBA Stats',
            'reason': ('Test fixture context' if testing else (
                'Operator rights basis configured' if nba_enabled else 'Source permission required'
            )),
            'rights_basis_configured': nba_basis,
        },
    }


def require_nba_stats_access():
    """Admit NBA network calls independently of permission for other providers."""
    if not provider_access_status()['nba_stats']['enabled']:
        raise requests.RequestException('NBA Stats source permission required before fetching data')


def _retry_delay(response, attempt):
    """Honor Retry-After; abandon this run if the provider asks a longer wait."""
    raw = response.headers.get('Retry-After') if response is not None else None
    if isinstance(raw, str):
        try:
            delay = float(raw)
        except ValueError:
            try:
                retry_at = parsedate_to_datetime(raw)
                if retry_at.tzinfo is None:
                    retry_at = retry_at.replace(tzinfo=timezone.utc)
                delay = (retry_at - datetime.now(timezone.utc)).total_seconds()
            except (ValueError, TypeError, OverflowError):
                delay = None
        if delay is not None and math.isfinite(delay):
            return None if delay > MAX_BACKOFF_SECONDS else max(0, delay)
    return min(MAX_BACKOFF_SECONDS, 0.25 * 2 ** (attempt - 1) + random.uniform(0, 0.25))


def request_with_retry(get, url, *, provider, attempts=MAX_ATTEMPTS, timeout=10,
                       before_attempt=None, on_response=None, **kwargs):
    """Return a successful response or a URL-free RequestException.

    Callbacks preserve transport-owned budget checks and response accounting.
    Only transient statuses/connections retry; malformed payloads are handled
    by the owning adapter. No exception retains a credential-bearing request.
    """
    try:
        attempts = max(1, min(int(attempts), MAX_ATTEMPTS))
    except (ValueError, TypeError, OverflowError):
        attempts = MAX_ATTEMPTS
    try:
        timeout = float(timeout)
        if not math.isfinite(timeout):
            timeout = MAX_TIMEOUT_SECONDS
        timeout = max(0.1, min(timeout, MAX_TIMEOUT_SECONDS))
    except (ValueError, TypeError, OverflowError):
        timeout = MAX_TIMEOUT_SECONDS
    reason = 'transport error'
    for attempt in range(1, attempts + 1):
        if before_attempt is not None:
            before_attempt()
        response = None
        try:
            response = get(url, timeout=timeout, **kwargs)
            if on_response is not None:
                on_response(response)
            response.raise_for_status()
            return response
        except requests.RequestException as error:
            if response is None:
                response = error.response
            status = getattr(response, 'status_code', None)
            status = status if isinstance(status, int) else None
            reason = f'HTTP {status}' if status is not None else type(error).__name__
            transient = (status == 429 or (status is not None and 500 <= status <= 599)
                         or (status is None and isinstance(
                             error, (requests.Timeout, requests.ConnectionError))))
            delay = _retry_delay(response, attempt) if transient else None
            if response is not None:
                response.close()
            if not transient or attempt == attempts or delay is None:
                break
            logger.warning('%s transient failure (%s); retry %d/%d',
                           provider, reason, attempt + 1, attempts)
            time.sleep(delay)
    raise requests.RequestException(f'{provider} request failed ({reason})') from None
