# Data Platform Foundations Implementation Plan (Phase 1, Plan A)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Permanent sport-aware historical game-log storage, a 3-season NBA backfill CLI, advanced box-score enrichment, and an Odds API budget manager — the data foundation for the distributional ML upgrade.

**Architecture:** A new `HistoricalGameLog` table (separate from the `PlayerGameLog` slate cache) stores per-game player rows for any sport, with common columns plus a per-sport `stats` JSON payload described by a `SPORT_STAT_CONFIG` registry. A `flask backfill-logs` command fills it from `nba_api`'s season-wide `LeagueGameLog` endpoint (1 API call per season), and `flask enrich-logs` adds usage/starter data per game. An `APIBudgetManager` wraps all The Odds API HTTP calls, tracking quota headers and refusing non-critical calls under a floor.

**Tech Stack:** Flask, SQLAlchemy, Alembic (flask-migrate), nba_api 1.5.2, requests, unittest.

## Global Constraints

- Test runner is **unittest** (never pytest); run with `SECRET_KEY=test`.
- Tests subclass `tests.helpers.BaseTestCase`; use `with self.app.app_context():` for DB work.
- All date logic uses ET: `from app.utils.time_helpers import ET` (a `ZoneInfo("America/New_York")`).
- Lint before every commit: `source .venv/bin/activate && ruff check . && bandit -q -r app -x tests -ll`.
- Never start APScheduler in tests/CLI — do not touch `_is_non_server_invocation()`.
- Git commits: plain messages, **no Co-Authored-By lines**.
- Activate the venv first in every shell: `source .venv/bin/activate`.
- Coverage gate is 80% — every new module needs tests.

---

### Task 1: Sport stat-config registry

**Files:**
- Create: `app/services/sport_config.py`
- Test: `tests/test_sport_config.py`

**Interfaces:**
- Produces: `SportStatConfig` frozen dataclass with fields `sport_key: str`, `stat_keys: tuple[str, ...]`; module-level dict `SPORT_STAT_CONFIG: dict[str, SportStatConfig]` with keys `'nba'`, `'mlb'`, `'nfl'`; function `get_stat_config(sport_key: str) -> SportStatConfig` raising `KeyError` for unknown sports. Later plans (feature builder, scenario engine) parameterize on this.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_sport_config.py
"""Tests for the per-sport stat catalog registry."""

from tests.helpers import BaseTestCase


class TestSportConfig(BaseTestCase):

    def test_nba_config_has_core_stats(self):
        from app.services.sport_config import get_stat_config
        cfg = get_stat_config('nba')
        self.assertEqual(cfg.sport_key, 'nba')
        for key in ('pts', 'reb', 'ast', 'stl', 'blk', 'fg3m', 'minutes'):
            self.assertIn(key, cfg.stat_keys)

    def test_mlb_and_nfl_configs_exist(self):
        from app.services.sport_config import SPORT_STAT_CONFIG
        self.assertIn('hits', SPORT_STAT_CONFIG['mlb'].stat_keys)
        self.assertIn('strikeouts_pitcher', SPORT_STAT_CONFIG['mlb'].stat_keys)
        self.assertIn('rec_yds', SPORT_STAT_CONFIG['nfl'].stat_keys)
        self.assertIn('pass_yds', SPORT_STAT_CONFIG['nfl'].stat_keys)

    def test_unknown_sport_raises_key_error(self):
        from app.services.sport_config import get_stat_config
        with self.assertRaises(KeyError):
            get_stat_config('cricket')

    def test_configs_are_immutable(self):
        from app.services.sport_config import get_stat_config
        cfg = get_stat_config('nba')
        with self.assertRaises(Exception):
            cfg.sport_key = 'other'
```

- [ ] **Step 2: Run test to verify it fails**

Run: `source .venv/bin/activate && SECRET_KEY=test python -m unittest tests.test_sport_config -v`
Expected: ERROR — `ModuleNotFoundError: No module named 'app.services.sport_config'`

- [ ] **Step 3: Write the implementation**

```python
# app/services/sport_config.py
"""Per-sport stat catalogs for HistoricalGameLog payloads.

The ``stats`` JSON column on ``HistoricalGameLog`` holds whatever keys the
sport's catalog defines.  Feature builders and the scenario engine iterate
``stat_keys`` instead of hard-coding NBA columns, which is what makes them
sport-agnostic.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class SportStatConfig:
    sport_key: str
    stat_keys: tuple[str, ...]


SPORT_STAT_CONFIG: dict[str, SportStatConfig] = {
    'nba': SportStatConfig(
        sport_key='nba',
        stat_keys=(
            'pts', 'reb', 'ast', 'stl', 'blk', 'tov',
            'fgm', 'fga', 'fg3m', 'fg3a', 'ftm', 'fta',
            'minutes', 'plus_minus', 'usage_pct',
        ),
    ),
    'mlb': SportStatConfig(
        sport_key='mlb',
        stat_keys=(
            'hits', 'total_bases', 'home_runs', 'rbis', 'runs',
            'stolen_bases', 'walks', 'strikeouts_batter',
            'strikeouts_pitcher', 'outs_recorded', 'earned_runs',
            'hits_allowed', 'walks_allowed',
        ),
    ),
    'nfl': SportStatConfig(
        sport_key='nfl',
        stat_keys=(
            'pass_yds', 'pass_tds', 'pass_attempts', 'completions',
            'interceptions', 'rush_yds', 'rush_attempts', 'rush_tds',
            'receptions', 'rec_yds', 'rec_tds', 'targets',
        ),
    ),
}


def get_stat_config(sport_key: str) -> SportStatConfig:
    """Return the stat catalog for a sport.  Raises KeyError if unknown."""
    return SPORT_STAT_CONFIG[sport_key]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `source .venv/bin/activate && SECRET_KEY=test python -m unittest tests.test_sport_config -v`
Expected: 4 tests PASS (`OK`)

- [ ] **Step 5: Lint and commit**

```bash
source .venv/bin/activate && ruff check . && bandit -q -r app -x tests -ll
git add app/services/sport_config.py tests/test_sport_config.py
git commit -m "feat: add per-sport stat catalog registry"
```

---

### Task 2: HistoricalGameLog model + migration

**Files:**
- Modify: `app/models.py` (append after `PlayerGameLog`, i.e. after line 468)
- Create: migration via `flask db migrate` (auto-generated file under `migrations/versions/`)
- Test: `tests/test_historical_game_log.py`

**Interfaces:**
- Consumes: nothing new (pure model).
- Produces: `HistoricalGameLog` SQLAlchemy model with columns `id`, `sport (str, default 'nba')`, `player_id (str)`, `player_name (str)`, `team_abbr (str|None)`, `opp_abbr (str|None)`, `game_id (str)`, `game_date (date)`, `season (str, e.g. '2025-26')`, `home_away ('HOME'|'AWAY'|None)`, `win_loss ('W'|'L'|None)`, `starter (bool|None)`, `stats (dict via JSON)`, `fetched_at (datetime)`. Unique on `(sport, player_id, game_id)`. All later plans read this table.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_historical_game_log.py
"""Tests for the permanent, sport-aware historical game log table."""

from datetime import date

from sqlalchemy.exc import IntegrityError

from app import db
from tests.helpers import BaseTestCase


def make_hist_row(**overrides):
    from app.models import HistoricalGameLog
    defaults = dict(
        sport='nba',
        player_id='2544',
        player_name='LeBron James',
        team_abbr='LAL',
        opp_abbr='BOS',
        game_id='0022400123',
        game_date=date(2026, 1, 15),
        season='2025-26',
        home_away='HOME',
        win_loss='W',
        starter=True,
        stats={'pts': 31.0, 'reb': 8.0, 'ast': 9.0},
    )
    defaults.update(overrides)
    return HistoricalGameLog(**defaults)


class TestHistoricalGameLog(BaseTestCase):

    def test_round_trip_with_json_stats(self):
        from app.models import HistoricalGameLog
        with self.app.app_context():
            db.session.add(make_hist_row())
            db.session.commit()
            row = HistoricalGameLog.query.one()
            self.assertEqual(row.sport, 'nba')
            self.assertEqual(row.stats['pts'], 31.0)
            self.assertEqual(row.season, '2025-26')
            self.assertTrue(row.starter)

    def test_unique_sport_player_game(self):
        with self.app.app_context():
            db.session.add(make_hist_row())
            db.session.commit()
            db.session.add(make_hist_row(stats={'pts': 99.0}))
            with self.assertRaises(IntegrityError):
                db.session.commit()
            db.session.rollback()

    def test_same_game_id_different_sport_allowed(self):
        from app.models import HistoricalGameLog
        with self.app.app_context():
            db.session.add(make_hist_row())
            db.session.add(make_hist_row(sport='mlb', stats={'hits': 2.0}))
            db.session.commit()
            self.assertEqual(HistoricalGameLog.query.count(), 2)

    def test_repr(self):
        row = make_hist_row()
        self.assertIn('LeBron James', repr(row))
        self.assertIn('nba', repr(row))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `source .venv/bin/activate && SECRET_KEY=test python -m unittest tests.test_historical_game_log -v`
Expected: ERROR — `ImportError: cannot import name 'HistoricalGameLog'`

- [ ] **Step 3: Add the model**

Append to `app/models.py` directly after the `PlayerGameLog` class (after its `__repr__`, line 468), following the file's existing style:

```python
class HistoricalGameLog(db.Model):
    """Permanent, sport-aware player game log used for model training.

    Unlike ``PlayerGameLog`` (a pruned slate cache), rows here are never
    deleted.  Common fields are real columns; per-sport stat payloads live
    in the ``stats`` JSON column, keyed per ``SPORT_STAT_CONFIG``.
    """

    id = db.Column(db.Integer, primary_key=True)
    sport = db.Column(db.String(10), nullable=False, default='nba', index=True)
    player_id = db.Column(db.String(20), nullable=False)
    player_name = db.Column(db.String(120), nullable=False)
    team_abbr = db.Column(db.String(10), nullable=True)
    opp_abbr = db.Column(db.String(10), nullable=True)
    game_id = db.Column(db.String(30), nullable=False)
    game_date = db.Column(db.Date, nullable=False)
    season = db.Column(db.String(10), nullable=False)
    home_away = db.Column(db.String(4), nullable=True)
    win_loss = db.Column(db.String(1), nullable=True)
    starter = db.Column(db.Boolean, nullable=True)
    stats = db.Column(db.JSON, nullable=False, default=dict)
    fetched_at = db.Column(
        db.DateTime, nullable=False, default=lambda: datetime.now(timezone.utc)
    )

    __table_args__ = (
        UniqueConstraint('sport', 'player_id', 'game_id',
                         name='uq_hist_sport_player_game'),
        Index('ix_hist_sport_player_date', 'sport', 'player_name', 'game_date'),
        Index('ix_hist_sport_season', 'sport', 'season'),
    )

    def __repr__(self) -> str:
        return f"<HistoricalGameLog {self.sport} {self.player_name} {self.game_date}>"
```

(`datetime`, `timezone`, `UniqueConstraint`, and `Index` are already imported at the top of `app/models.py` — verify before assuming, and add imports only if missing.)

- [ ] **Step 4: Run test to verify it passes**

Run: `source .venv/bin/activate && SECRET_KEY=test python -m unittest tests.test_historical_game_log -v`
Expected: 4 tests PASS. (`BaseTestCase` uses `db.create_all()`, so no migration is needed for tests.)

- [ ] **Step 5: Generate and apply the migration**

```bash
source .venv/bin/activate && export $(grep -v '^#' .env | grep -v '^\s*$' | xargs) 2>/dev/null
flask --app run.py db migrate -m "add historical_game_log table"
flask --app run.py db upgrade heads
```

Then open the generated file in `migrations/versions/` and verify it creates exactly one table (`historical_game_log`) with the unique constraint and both indexes — remove any unrelated auto-detected changes.

- [ ] **Step 6: Verify the full suite still passes**

Run: `source .venv/bin/activate && SECRET_KEY=test python -m unittest discover -s tests`
Expected: all tests pass.

- [ ] **Step 7: Lint and commit**

```bash
source .venv/bin/activate && ruff check . && bandit -q -r app -x tests -ll
git add app/models.py tests/test_historical_game_log.py migrations/versions/
git commit -m "feat: add permanent sport-aware HistoricalGameLog table"
```

---

### Task 3: APIBudgetManager service

**Files:**
- Create: `app/services/api_budget.py`
- Test: `tests/test_api_budget.py`

**Interfaces:**
- Produces:
  - `class BudgetExhaustedError(requests.RequestException)` — subclasses `RequestException` **deliberately** so every existing `except requests.RequestException` handler in `nba_service.py` degrades gracefully without modification.
  - `class APIBudgetManager` with: `record_headers(headers) -> None` (reads `x-requests-remaining` / `x-requests-used`, case-insensitive), `remaining` property (`float | None`), `can_spend(critical: bool = False) -> bool`, and `budgeted_get(url, params=None, timeout=10, critical=False) -> requests.Response` (raises `BudgetExhaustedError` before making the call if `can_spend` is False; records headers after).
  - Module singleton `ODDS_BUDGET = APIBudgetManager()` — used by Task 4.
  - Floor from env `ODDS_API_BUDGET_FLOOR` (default `25`). Unknown remaining (never seen a header) → spending allowed.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_api_budget.py
"""Tests for the Odds API request budget manager."""

from unittest.mock import MagicMock, patch

from tests.helpers import BaseTestCase


class TestAPIBudgetManager(BaseTestCase):

    def _manager(self, floor=25):
        from app.services.api_budget import APIBudgetManager
        return APIBudgetManager(floor=floor)

    def test_unknown_budget_allows_spending(self):
        mgr = self._manager()
        self.assertIsNone(mgr.remaining)
        self.assertTrue(mgr.can_spend())
        self.assertTrue(mgr.can_spend(critical=True))

    def test_records_quota_headers_case_insensitive(self):
        mgr = self._manager()
        mgr.record_headers({'X-Requests-Remaining': '123.0', 'X-Requests-Used': '377'})
        self.assertEqual(mgr.remaining, 123.0)

    def test_blocks_non_critical_below_floor_allows_critical(self):
        mgr = self._manager(floor=50)
        mgr.record_headers({'x-requests-remaining': '10'})
        self.assertFalse(mgr.can_spend())
        self.assertTrue(mgr.can_spend(critical=True))

    def test_malformed_headers_ignored(self):
        mgr = self._manager()
        mgr.record_headers({'x-requests-remaining': 'garbage'})
        self.assertIsNone(mgr.remaining)
        self.assertTrue(mgr.can_spend())

    @patch('app.services.api_budget.requests.get')
    def test_budgeted_get_records_headers(self, mock_get):
        resp = MagicMock()
        resp.headers = {'x-requests-remaining': '99'}
        mock_get.return_value = resp
        mgr = self._manager()
        out = mgr.budgeted_get('https://example.com', params={'a': 1}, timeout=5)
        self.assertIs(out, resp)
        self.assertEqual(mgr.remaining, 99.0)
        mock_get.assert_called_once_with(
            'https://example.com', params={'a': 1}, timeout=5
        )

    @patch('app.services.api_budget.requests.get')
    def test_budgeted_get_raises_when_exhausted(self, mock_get):
        from app.services.api_budget import BudgetExhaustedError
        mgr = self._manager(floor=50)
        mgr.record_headers({'x-requests-remaining': '5'})
        with self.assertRaises(BudgetExhaustedError):
            mgr.budgeted_get('https://example.com')
        mock_get.assert_not_called()

    def test_exhausted_error_is_request_exception(self):
        import requests
        from app.services.api_budget import BudgetExhaustedError
        self.assertTrue(issubclass(BudgetExhaustedError, requests.RequestException))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `source .venv/bin/activate && SECRET_KEY=test python -m unittest tests.test_api_budget -v`
Expected: ERROR — `ModuleNotFoundError: No module named 'app.services.api_budget'`

- [ ] **Step 3: Write the implementation**

```python
# app/services/api_budget.py
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `source .venv/bin/activate && SECRET_KEY=test python -m unittest tests.test_api_budget -v`
Expected: 7 tests PASS.

- [ ] **Step 5: Lint and commit**

```bash
source .venv/bin/activate && ruff check . && bandit -q -r app -x tests -ll
git add app/services/api_budget.py tests/test_api_budget.py
git commit -m "feat: add Odds API request budget manager"
```

---

### Task 4: Wire budget manager into all Odds API call sites

**Files:**
- Modify: `app/services/nba_service.py`
- Test: `tests/test_api_budget_wiring.py`

**Interfaces:**
- Consumes: `ODDS_BUDGET.budgeted_get(url, params=, timeout=, critical=)` and `BudgetExhaustedError` from Task 3.
- Produces: every HTTP call to `api.the-odds-api.com` in `nba_service.py` goes through `ODDS_BUDGET`. ESPN calls are untouched. Behavior on refusal: identical to a network error today (empty result + warning log), because `BudgetExhaustedError` is a `requests.RequestException`.

- [ ] **Step 1: Find every Odds API call site**

Run: `grep -n "requests.get" app/services/nba_service.py`

Classify each hit: sites whose URL constant contains `the-odds-api.com` (`ODDS_API_URL`, `ODDS_API_EVENTS_URL`, the historical endpoint near line 545, and the player-props fetch) are in scope. ESPN sites (`ESPN_SCOREBOARD_URL`, `ESPN_SUMMARY_URL`) are **out of scope — do not touch**.

- [ ] **Step 2: Write the failing test**

```python
# tests/test_api_budget_wiring.py
"""Verify Odds API calls in nba_service route through the budget manager."""

from unittest.mock import MagicMock, patch

from tests.helpers import BaseTestCase


def _fake_response(json_payload):
    resp = MagicMock()
    resp.json.return_value = json_payload
    resp.headers = {'x-requests-remaining': '400'}
    resp.raise_for_status.return_value = None
    return resp


class TestBudgetWiring(BaseTestCase):

    @patch.dict('os.environ', {'ODDS_API_KEY': 'test-key'})
    @patch('app.services.api_budget.requests.get')
    def test_fetch_odds_combined_uses_budgeted_get(self, mock_get):
        mock_get.return_value = _fake_response([])
        from app.services import nba_service
        from app.services.api_budget import ODDS_BUDGET
        totals, h2h = nba_service.fetch_odds_combined()
        self.assertEqual((totals, h2h), ({}, {}))
        mock_get.assert_called_once()          # went through api_budget module
        self.assertEqual(ODDS_BUDGET.remaining, 400.0)

    @patch.dict('os.environ', {'ODDS_API_KEY': 'test-key'})
    @patch('app.services.api_budget.requests.get')
    def test_fetch_odds_events_uses_budgeted_get(self, mock_get):
        mock_get.return_value = _fake_response([])
        from app.services import nba_service
        result = nba_service.fetch_odds_events()
        self.assertEqual(result, {})
        mock_get.assert_called_once()

    @patch.dict('os.environ', {'ODDS_API_KEY': 'test-key'})
    def test_budget_exhaustion_degrades_to_empty(self):
        from app.services import nba_service
        from app.services.api_budget import ODDS_BUDGET
        ODDS_BUDGET.record_headers({'x-requests-remaining': '1'})
        try:
            totals, h2h = nba_service.fetch_odds_combined()
            self.assertEqual((totals, h2h), ({}, {}))
        finally:
            ODDS_BUDGET._remaining = None   # reset singleton for other tests
```

Note: the singleton reset in the last test is required because `ODDS_BUDGET` is process-wide; without it, later tests in the same run could be starved.

- [ ] **Step 3: Run test to verify it fails**

Run: `source .venv/bin/activate && SECRET_KEY=test python -m unittest tests.test_api_budget_wiring -v`
Expected: first two tests FAIL (`mock_get.assert_called_once()` fails — `nba_service` still calls `requests.get` directly, not through `app.services.api_budget`).

- [ ] **Step 4: Rewire the call sites**

In `app/services/nba_service.py`, add the import near the top with the other app imports:

```python
from app.services.api_budget import ODDS_BUDGET
```

Then at **each** Odds API site found in Step 1, replace `requests.get(` with `ODDS_BUDGET.budgeted_get(`. Example — `fetch_odds_combined` (lines 194–203) becomes:

```python
        resp = ODDS_BUDGET.budgeted_get(
            ODDS_API_URL,
            params={
                "apiKey": api_key,
                "regions": "us",
                "markets": "totals,h2h",
                "oddsFormat": "american",
            },
            timeout=10,
        )
```

Apply the same one-line substitution at: the historical odds fetch (~line 552), `_fetch_standard_odds_for_date_window` (~line 590), `fetch_odds_events` (~line 719), and the player-props fetch. Pass `critical=True` at exactly one site: the historical odds fetch used by bet resolution (~552) — grading must not be starved. All others stay non-critical (default). Do not change any `except` clauses: `BudgetExhaustedError` is already a `requests.RequestException`.

- [ ] **Step 5: Run wiring tests and the full suite**

Run: `source .venv/bin/activate && SECRET_KEY=test python -m unittest tests.test_api_budget_wiring -v`
Expected: 3 tests PASS.

Run: `SECRET_KEY=test python -m unittest discover -s tests`
Expected: all pass (existing nba_service tests that patch `requests.get` inside `nba_service` may need their patch target updated to `app.services.api_budget.requests.get` — if any fail, that is the fix, not changing the wiring).

- [ ] **Step 6: Lint and commit**

```bash
source .venv/bin/activate && ruff check . && bandit -q -r app -x tests -ll
git add app/services/nba_service.py tests/test_api_budget_wiring.py
git commit -m "feat: route all Odds API calls through the budget manager"
```

---

### Task 5: `flask backfill-logs` — season backfill into HistoricalGameLog

**Files:**
- Create: `app/cli/history_commands.py`
- Modify: `app/cli/__init__.py` (`register_cli`, lines 90–116 — add the new module alongside the existing four `register_*` imports/calls)
- Test: `tests/test_history_commands.py`

**Interfaces:**
- Consumes: `HistoricalGameLog` (Task 2), `extract_opp_abbr(matchup)` from `app/services/ml_feature_builder.py`, `JobLog` model (fields: `job_name`, `started_at`, `finished_at`, `status`, `message`).
- Produces: CLI `flask backfill-logs --sport nba --seasons 3 [--season-type "Regular Season"] [--sleep 1.5]`; helper functions `_recent_seasons(n: int, today: date) -> list[str]` and `_rows_from_league_log(df, season: str) -> list[dict]` (returns kwargs dicts for `HistoricalGameLog`). Uses `nba_api.stats.endpoints.leaguegamelog.LeagueGameLog` — **one API call per season** (this is the API-call-reduction strategy; never per-player calls). Idempotent: existing `(sport, player_id, game_id)` keys are skipped, so `--resume` is implicit.

- [ ] **Step 1: Write the failing tests (helpers first)**

```python
# tests/test_history_commands.py
"""Tests for the historical game-log backfill CLI."""

from datetime import date
from unittest.mock import patch

import pandas as pd

from app import db
from tests.helpers import BaseTestCase


def _league_log_df():
    """Two players, one game — column names match nba_api LeagueGameLog."""
    return pd.DataFrame([
        {
            'PLAYER_ID': 2544, 'PLAYER_NAME': 'LeBron James',
            'TEAM_ABBREVIATION': 'LAL', 'GAME_ID': '0022500001',
            'GAME_DATE': '2025-10-21', 'MATCHUP': 'LAL vs. BOS', 'WL': 'W',
            'MIN': 36, 'PTS': 28, 'REB': 7, 'AST': 11, 'STL': 1, 'BLK': 0,
            'TOV': 3, 'FGM': 10, 'FGA': 19, 'FG3M': 2, 'FG3A': 6,
            'FTM': 6, 'FTA': 7, 'PLUS_MINUS': 12,
        },
        {
            'PLAYER_ID': 1628369, 'PLAYER_NAME': 'Jayson Tatum',
            'TEAM_ABBREVIATION': 'BOS', 'GAME_ID': '0022500001',
            'GAME_DATE': '2025-10-21', 'MATCHUP': 'BOS @ LAL', 'WL': 'L',
            'MIN': 38, 'PTS': 33, 'REB': 9, 'AST': 5, 'STL': 2, 'BLK': 1,
            'TOV': 2, 'FGM': 12, 'FGA': 24, 'FG3M': 4, 'FG3A': 11,
            'FTM': 5, 'FTA': 5, 'PLUS_MINUS': -12,
        },
    ])


class TestSeasonHelpers(BaseTestCase):

    def test_recent_seasons_mid_offseason(self):
        from app.cli.history_commands import _recent_seasons
        # July 2026 → most recent completed/active season is 2025-26
        self.assertEqual(
            _recent_seasons(3, today=date(2026, 7, 7)),
            ['2025-26', '2024-25', '2023-24'],
        )

    def test_recent_seasons_after_october_rolls_forward(self):
        from app.cli.history_commands import _recent_seasons
        self.assertEqual(
            _recent_seasons(2, today=date(2026, 11, 1)),
            ['2026-27', '2025-26'],
        )


class TestRowsFromLeagueLog(BaseTestCase):

    def test_maps_columns_and_derives_context(self):
        from app.cli.history_commands import _rows_from_league_log
        rows = _rows_from_league_log(_league_log_df(), season='2025-26')
        self.assertEqual(len(rows), 2)
        lebron = rows[0]
        self.assertEqual(lebron['player_id'], '2544')
        self.assertEqual(lebron['sport'], 'nba')
        self.assertEqual(lebron['opp_abbr'], 'BOS')
        self.assertEqual(lebron['home_away'], 'HOME')
        self.assertEqual(lebron['game_date'], date(2025, 10, 21))
        self.assertEqual(lebron['stats']['pts'], 28.0)
        self.assertEqual(lebron['stats']['minutes'], 36.0)
        tatum = rows[1]
        self.assertEqual(tatum['home_away'], 'AWAY')
        self.assertEqual(tatum['opp_abbr'], 'LAL')


class TestBackfillCommand(BaseTestCase):

    def _run(self, args):
        runner = self.app.test_cli_runner()
        from app.cli.history_commands import cli_backfill_logs
        return runner.invoke(cli_backfill_logs, args)

    @patch('app.cli.history_commands._fetch_league_log_df')
    def test_backfill_inserts_and_logs(self, mock_fetch):
        from app.models import HistoricalGameLog, JobLog
        mock_fetch.return_value = _league_log_df()
        result = self._run(['--sport', 'nba', '--seasons', '1', '--sleep', '0'])
        self.assertEqual(result.exit_code, 0, result.output)
        with self.app.app_context():
            self.assertEqual(HistoricalGameLog.query.count(), 2)
            job = JobLog.query.filter_by(job_name='backfill-logs').one()
            self.assertEqual(job.status, 'success')

    @patch('app.cli.history_commands._fetch_league_log_df')
    def test_backfill_is_idempotent(self, mock_fetch):
        from app.models import HistoricalGameLog
        mock_fetch.return_value = _league_log_df()
        self._run(['--sport', 'nba', '--seasons', '1', '--sleep', '0'])
        result = self._run(['--sport', 'nba', '--seasons', '1', '--sleep', '0'])
        self.assertEqual(result.exit_code, 0, result.output)
        with self.app.app_context():
            self.assertEqual(HistoricalGameLog.query.count(), 2)

    def test_non_nba_sport_rejected_for_now(self):
        result = self._run(['--sport', 'mlb', '--seasons', '1'])
        self.assertNotEqual(result.exit_code, 0)

    @patch('app.cli.history_commands._fetch_league_log_df')
    def test_season_fetch_failure_marks_job_failed(self, mock_fetch):
        from app.models import JobLog
        mock_fetch.side_effect = RuntimeError('nba_api down')
        result = self._run(['--sport', 'nba', '--seasons', '1', '--sleep', '0'])
        self.assertEqual(result.exit_code, 0)  # command reports, doesn't crash
        with self.app.app_context():
            job = JobLog.query.filter_by(job_name='backfill-logs').one()
            self.assertEqual(job.status, 'failed')
            self.assertIn('nba_api down', job.message)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `source .venv/bin/activate && SECRET_KEY=test python -m unittest tests.test_history_commands -v`
Expected: ERROR — `ModuleNotFoundError: No module named 'app.cli.history_commands'`

- [ ] **Step 3: Write the implementation**

```python
# app/cli/history_commands.py
"""CLI commands for the permanent HistoricalGameLog store."""

import logging
import time
from datetime import date, datetime, timezone

import click

from app import db
from app.models import HistoricalGameLog, JobLog
from app.services.ml_feature_builder import extract_opp_abbr
from app.utils.time_helpers import ET

logger = logging.getLogger(__name__)

# LeagueGameLog column → stats-payload key (all coerced to float)
_NBA_STAT_COLUMNS = {
    'PTS': 'pts', 'REB': 'reb', 'AST': 'ast', 'STL': 'stl', 'BLK': 'blk',
    'TOV': 'tov', 'FGM': 'fgm', 'FGA': 'fga', 'FG3M': 'fg3m', 'FG3A': 'fg3a',
    'FTM': 'ftm', 'FTA': 'fta', 'MIN': 'minutes', 'PLUS_MINUS': 'plus_minus',
}


def _recent_seasons(n: int, today: date | None = None) -> list[str]:
    """Most recent ``n`` NBA season strings, newest first.

    NBA seasons start in October: before October, the 'current' season is
    the one that started last calendar year.
    """
    today = today or datetime.now(ET).date()
    start_year = today.year if today.month >= 10 else today.year - 1
    return [
        f"{y}-{str(y + 1)[-2:]}"
        for y in range(start_year, start_year - n, -1)
    ]


def _fetch_league_log_df(season: str, season_type: str):
    """One nba_api call for a full season of player game logs."""
    from nba_api.stats.endpoints import leaguegamelog
    log = leaguegamelog.LeagueGameLog(
        season=season,
        season_type_all_star=season_type,
        player_or_team_abbreviation='P',
        timeout=60,
    )
    return log.get_data_frames()[0]


def _rows_from_league_log(df, season: str) -> list[dict]:
    """Map a LeagueGameLog dataframe to HistoricalGameLog constructor kwargs."""
    rows = []
    for rec in df.to_dict('records'):
        matchup = str(rec.get('MATCHUP') or '')
        stats = {}
        for col, key in _NBA_STAT_COLUMNS.items():
            try:
                stats[key] = float(rec.get(col) or 0.0)
            except (TypeError, ValueError):
                stats[key] = 0.0
        rows.append(dict(
            sport='nba',
            player_id=str(rec.get('PLAYER_ID', '')),
            player_name=str(rec.get('PLAYER_NAME', '')),
            team_abbr=str(rec.get('TEAM_ABBREVIATION') or '') or None,
            opp_abbr=extract_opp_abbr(matchup) or None,
            game_id=str(rec.get('GAME_ID', '')),
            game_date=datetime.strptime(
                str(rec.get('GAME_DATE', '')), '%Y-%m-%d').date(),
            season=season,
            home_away='HOME' if ' vs. ' in matchup else 'AWAY',
            win_loss=str(rec.get('WL') or '') or None,
            starter=None,          # filled by `flask enrich-logs`
            stats=stats,
        ))
    return rows


@click.command('backfill-logs')
@click.option('--sport', default='nba', show_default=True)
@click.option('--seasons', default=3, show_default=True, type=int)
@click.option('--season-type', default='Regular Season', show_default=True)
@click.option('--sleep', 'sleep_seconds', default=1.5, show_default=True,
              type=float, help='Pause between season fetches (rate limit).')
def cli_backfill_logs(sport, seasons, season_type, sleep_seconds):
    """Backfill HistoricalGameLog from season-wide league game logs."""
    if sport != 'nba':
        raise click.BadParameter(
            f"sport '{sport}' not supported yet (nba only; mlb/nfl are "
            "Phase 3/4)")

    job = JobLog(job_name='backfill-logs',
                 started_at=datetime.now(timezone.utc), status='running')
    db.session.add(job)
    db.session.commit()

    inserted = skipped = 0
    errors: list[str] = []

    for season in _recent_seasons(seasons):
        try:
            df = _fetch_league_log_df(season, season_type)
        except Exception as exc:  # nba_api raises assorted exception types
            errors.append(f"{season}: {exc}")
            logger.error("backfill-logs: season %s fetch failed: %s",
                         season, exc)
            continue

        existing = {
            (pid, gid) for pid, gid in db.session.query(
                HistoricalGameLog.player_id, HistoricalGameLog.game_id,
            ).filter_by(sport=sport, season=season)
        }
        batch = []
        for kwargs in _rows_from_league_log(df, season):
            if (kwargs['player_id'], kwargs['game_id']) in existing:
                skipped += 1
                continue
            batch.append(HistoricalGameLog(**kwargs))
        db.session.add_all(batch)
        db.session.commit()
        inserted += len(batch)
        click.echo(f"{season}: +{len(batch)} rows ({skipped} already present)")
        if sleep_seconds:
            time.sleep(sleep_seconds)

    job.finished_at = datetime.now(timezone.utc)
    job.status = 'failed' if errors else 'success'
    job.message = (
        f"inserted={inserted} skipped={skipped}"
        + (f" errors={'; '.join(errors)}" if errors else "")
    )
    db.session.commit()
    click.echo(f"Done: {job.message}")


def register_history_commands(app):
    app.cli.add_command(cli_backfill_logs)
```

- [ ] **Step 4: Register in `app/cli/__init__.py`**

In `register_cli` (line 90), alongside the existing four imports add:

```python
    from app.cli.history_commands import register_history_commands
```

and alongside the existing `register_*(app)` calls add:

```python
    register_history_commands(app)
```

(Place both in the same positions/pattern as `register_stats_commands` — the app-context wrapping loop at lines 102–116 picks the new command up automatically.)

- [ ] **Step 5: Run tests to verify they pass**

Run: `source .venv/bin/activate && SECRET_KEY=test python -m unittest tests.test_history_commands -v`
Expected: 7 tests PASS.

- [ ] **Step 6: Full suite, lint, commit**

```bash
source .venv/bin/activate && SECRET_KEY=test python -m unittest discover -s tests
ruff check . && bandit -q -r app -x tests -ll
git add app/cli/history_commands.py app/cli/__init__.py tests/test_history_commands.py
git commit -m "feat: add backfill-logs CLI for historical game logs"
```

---

### Task 6: `flask enrich-logs` — advanced box-score enrichment

**Files:**
- Modify: `app/cli/history_commands.py` (add command + helper)
- Test: `tests/test_history_commands.py` (append test class)

**Interfaces:**
- Consumes: `HistoricalGameLog` rows where `starter IS NULL` (the marker meaning "not yet enriched" — set by Task 5).
- Produces: CLI `flask enrich-logs --sport nba [--limit 100] [--sleep 0.8]`. For each un-enriched NBA game (distinct `game_id`), one call to `nba_api.stats.endpoints.boxscoreadvancedv2.BoxScoreAdvancedV2` merges `usage_pct` into the `stats` payload and sets `starter` from `START_POSITION`. Resumable by construction: enriched rows have non-NULL `starter` and are never re-fetched. `--limit` bounds games per run so it can be chunked.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_history_commands.py`:

```python
class TestEnrichCommand(BaseTestCase):

    def _seed_two_rows(self):
        from tests.test_historical_game_log import make_hist_row
        with self.app.app_context():
            db.session.add(make_hist_row(
                player_id='2544', starter=None,
                stats={'pts': 28.0, 'minutes': 36.0}))
            db.session.add(make_hist_row(
                player_id='1628369', player_name='Jayson Tatum',
                team_abbr='BOS', opp_abbr='LAL', home_away='AWAY',
                starter=None, stats={'pts': 33.0, 'minutes': 38.0}))
            db.session.commit()

    def _advanced_df(self):
        return pd.DataFrame([
            {'PLAYER_ID': 2544, 'START_POSITION': 'F', 'USG_PCT': 0.31},
            {'PLAYER_ID': 1628369, 'START_POSITION': '', 'USG_PCT': 0.28},
        ])

    def _run(self, args):
        runner = self.app.test_cli_runner()
        from app.cli.history_commands import cli_enrich_logs
        return runner.invoke(cli_enrich_logs, args)

    @patch('app.cli.history_commands._fetch_advanced_boxscore_df')
    def test_enrich_sets_starter_and_usage(self, mock_fetch):
        from app.models import HistoricalGameLog
        self._seed_two_rows()
        mock_fetch.return_value = self._advanced_df()
        result = self._run(['--sport', 'nba', '--sleep', '0'])
        self.assertEqual(result.exit_code, 0, result.output)
        mock_fetch.assert_called_once_with('0022400123')
        with self.app.app_context():
            lebron = HistoricalGameLog.query.filter_by(player_id='2544').one()
            self.assertTrue(lebron.starter)
            self.assertAlmostEqual(lebron.stats['usage_pct'], 0.31)
            self.assertEqual(lebron.stats['pts'], 28.0)   # payload preserved
            tatum = HistoricalGameLog.query.filter_by(player_id='1628369').one()
            self.assertFalse(tatum.starter)               # empty START_POSITION

    @patch('app.cli.history_commands._fetch_advanced_boxscore_df')
    def test_enrich_skips_already_enriched(self, mock_fetch):
        self._seed_two_rows()
        mock_fetch.return_value = self._advanced_df()
        self._run(['--sport', 'nba', '--sleep', '0'])
        mock_fetch.reset_mock()
        self._run(['--sport', 'nba', '--sleep', '0'])
        mock_fetch.assert_not_called()

    @patch('app.cli.history_commands._fetch_advanced_boxscore_df')
    def test_enrich_respects_limit(self, mock_fetch):
        from tests.test_historical_game_log import make_hist_row
        self._seed_two_rows()
        with self.app.app_context():
            db.session.add(make_hist_row(
                game_id='0022400999', starter=None, stats={'pts': 10.0}))
            db.session.commit()
        mock_fetch.return_value = self._advanced_df()
        self._run(['--sport', 'nba', '--limit', '1', '--sleep', '0'])
        self.assertEqual(mock_fetch.call_count, 1)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `source .venv/bin/activate && SECRET_KEY=test python -m unittest tests.test_history_commands.TestEnrichCommand -v`
Expected: ERROR — `ImportError: cannot import name 'cli_enrich_logs'`

- [ ] **Step 3: Write the implementation**

Append to `app/cli/history_commands.py`:

```python
def _fetch_advanced_boxscore_df(game_id: str):
    """One nba_api call: advanced box score (USG_PCT, START_POSITION)."""
    from nba_api.stats.endpoints import boxscoreadvancedv2
    box = boxscoreadvancedv2.BoxScoreAdvancedV2(game_id=game_id, timeout=60)
    return box.get_data_frames()[0]   # player-level frame


@click.command('enrich-logs')
@click.option('--sport', default='nba', show_default=True)
@click.option('--limit', default=200, show_default=True, type=int,
              help='Max games to enrich this run (chunkable).')
@click.option('--sleep', 'sleep_seconds', default=0.8, show_default=True,
              type=float)
def cli_enrich_logs(sport, limit, sleep_seconds):
    """Merge advanced box-score data (usage, starter) into HistoricalGameLog.

    Rows with ``starter IS NULL`` are un-enriched; one API call per game.
    """
    if sport != 'nba':
        raise click.BadParameter(f"sport '{sport}' not supported yet")

    pending_games = [
        gid for (gid,) in db.session.query(HistoricalGameLog.game_id)
        .filter_by(sport=sport)
        .filter(HistoricalGameLog.starter.is_(None))
        .distinct().order_by(HistoricalGameLog.game_id)
        .limit(limit)
    ]
    enriched = failed = 0
    for gid in pending_games:
        try:
            df = _fetch_advanced_boxscore_df(gid)
        except Exception as exc:
            failed += 1
            logger.warning("enrich-logs: game %s fetch failed: %s", gid, exc)
            continue
        by_player = {
            str(rec.get('PLAYER_ID', '')): rec for rec in df.to_dict('records')
        }
        rows = HistoricalGameLog.query.filter_by(
            sport=sport, game_id=gid).all()
        for row in rows:
            rec = by_player.get(row.player_id)
            if rec is None:
                continue
            row.starter = bool(str(rec.get('START_POSITION') or '').strip())
            new_stats = dict(row.stats or {})
            try:
                new_stats['usage_pct'] = float(rec.get('USG_PCT') or 0.0)
            except (TypeError, ValueError):
                new_stats['usage_pct'] = 0.0
            row.stats = new_stats   # reassign — JSON columns don't track mutation
        db.session.commit()
        enriched += 1
        if sleep_seconds:
            time.sleep(sleep_seconds)

    click.echo(f"Enriched {enriched} games ({failed} failed, "
               f"{len(pending_games)} attempted)")
```

And register it inside `register_history_commands`:

```python
def register_history_commands(app):
    app.cli.add_command(cli_backfill_logs)
    app.cli.add_command(cli_enrich_logs)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `source .venv/bin/activate && SECRET_KEY=test python -m unittest tests.test_history_commands -v`
Expected: 10 tests PASS (7 from Task 5 + 3 new).

- [ ] **Step 5: Full suite, lint, commit**

```bash
source .venv/bin/activate && SECRET_KEY=test python -m unittest discover -s tests
ruff check . && bandit -q -r app -x tests -ll
git add app/cli/history_commands.py tests/test_history_commands.py
git commit -m "feat: add enrich-logs CLI for advanced box-score data"
```

---

### Task 7: Run the real backfill and verify

**Files:** none (operational verification).

**Interfaces:**
- Consumes: Tasks 2, 5, 6 complete; `.env` with real config; network access to stats.nba.com.

- [ ] **Step 1: Apply migrations and run the backfill**

```bash
source .venv/bin/activate && export $(grep -v '^#' .env | grep -v '^\s*$' | xargs) 2>/dev/null
flask --app run.py db upgrade heads
flask --app run.py backfill-logs --sport nba --seasons 3
```

Expected: three `season: +N rows` lines, each N roughly 26,000–30,000 (a full NBA regular season of player-games), then `Done: inserted=... skipped=0`.

- [ ] **Step 2: Sanity-check the data**

```bash
flask --app run.py shell -c "
from app.models import HistoricalGameLog
from app import db
print('total:', HistoricalGameLog.query.count())
print('seasons:', db.session.query(HistoricalGameLog.season, db.func.count()).group_by(HistoricalGameLog.season).all())
row = HistoricalGameLog.query.filter_by(player_name='LeBron James').order_by(HistoricalGameLog.game_date.desc()).first()
print('spot check:', row, row.stats if row else None)
"
```

Expected: total ≥ 75,000; three seasons listed; the spot-check row's `pts` matches nba.com for that date (verify manually in a browser).

- [ ] **Step 3: Enrich a first chunk**

```bash
flask --app run.py enrich-logs --sport nba --limit 50
```

Expected: `Enriched 50 games (0 failed, 50 attempted)`. Full enrichment (~3,700 games at ~0.8 s each ≈ 1 hour per season) can run chunked in the background afterward; it is resumable by design.

- [ ] **Step 4: Record the JobLog outcome**

```bash
flask --app run.py shell -c "
from app.models import JobLog
print(JobLog.query.filter_by(job_name='backfill-logs').order_by(JobLog.id.desc()).first().message)
"
```

Expected: `inserted=... skipped=...` with no `errors=` segment. If errors are present, re-run `backfill-logs` (idempotent) before proceeding to Plan B.
