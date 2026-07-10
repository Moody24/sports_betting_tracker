# Plan A2: Game-Day Coordinator Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Game-aware scheduling — a 5-minute coordinator job that tiers its polling to the live NBA slate, fires grade→postmortem→snapshot-finalize→history-append chains the tick after each game goes final, self-heals after downtime, and keeps HistoricalGameLog current.

**Architecture:** One new service module for the tick state machine (`game_day_coordinator.py`), one for ESPN box-score → HistoricalGameLog appends (`espn_history_append.py`), a shared mapping module extracted from the hoopR importer, plus scheduler/CLI wiring. The DB is the event log: Bet outcomes, `GameSnapshot.is_final`, and HistoricalGameLog row presence define what still needs doing. Spec: `docs/superpowers/specs/2026-07-10-plan-a2-game-day-coordinator-design.md`.

**Tech Stack:** Flask 3.1, SQLAlchemy, APScheduler (CronTrigger), requests (ESPN endpoints, free), pandas only where already used. unittest + coverage, ruff + bandit.

## Global Constraints

- All date/window logic in ET: `from app.utils.time_helpers import ET` (`ZoneInfo("America/New_York")`).
- HistoricalGameLog rows use the ESPN id namespace (string athlete_id / ESPN game_id) — identical conventions to `app/cli/hoopr_import.py` (abbr normalization, NBA-30 validation, usage formula, stats keys).
- Never re-fetch persisted data: check HistoricalGameLog before any ESPN summary call.
- LIVE-tier scoreboard reads must not pass through any cache with TTL ≥ 5 min (use `app.services.nba_service.fetch_espn_scoreboard` directly — it is uncached; do NOT use context_service's 10-min today cache).
- Test runner: `SECRET_KEY=test python -m unittest tests.<module> -v` (unittest, NOT pytest). Full suite + `ruff check .` + `bandit -q -r app -x tests -ll` before every commit. Run test suites in the foreground.
- Tests must not hit the network: patch `fetch_espn_scoreboard` / `requests.get` everywhere.
- Commits: conventional style (`feat:`/`fix:`/`test:`), NO Co-Authored-By lines ever.
- Scheduler jobs must be registered inside `init_scheduler(app)` in `app/services/scheduler.py` with `replace_existing=True` and `_log_job(...)` wrappers, matching existing style.
- New CLI commands follow `app/cli/history_commands.py` style (click, registered via a `register_*` function called in `app/cli/__init__.py:register_cli`).

## File Structure

- Create `app/services/espn_mapping.py` — shared ESPN↔NBA mapping constants + usage/season helpers (moved out of `app/cli/hoopr_import.py`).
- Create `app/services/espn_history_append.py` — fetch/parse ESPN summary box score, build + insert HistoricalGameLog rows (no-refetch guard).
- Create `app/services/game_day_coordinator.py` — tick state machine, newly-final detection, chain orchestration, 3-day lookback, day-verdict memo.
- Modify `app/cli/hoopr_import.py` — import shared constants from `espn_mapping` (behavior unchanged); extract callable `import_hoopr_seasons()` core.
- Modify `app/services/scheduler.py` — register `game_day_coordinator` (*/5 min) + `hoopr_reconcile` (Sun 08:20 ET) jobs; games-today guard in `snapshot_props_odds`.
- Modify `app/cli/__init__.py` — register new `coordinator-tick` CLI.
- Create `app/cli/coordinator_commands.py` — `flask coordinator-tick` manual command.
- Tests: `tests/test_espn_mapping.py`, `tests/test_espn_history_append.py`, `tests/test_game_day_coordinator.py`, additions to `tests/test_hoopr_import.py`.

---

### Task 1: Shared ESPN mapping module

**Files:**
- Create: `app/services/espn_mapping.py`
- Modify: `app/cli/hoopr_import.py` (import from the new module; delete the moved constants)
- Test: `tests/test_espn_mapping.py`

**Interfaces:**
- Consumes: nothing new.
- Produces (used by Tasks 2, 3): `ESPN_TO_NBA_ABBR: dict[str,str]`, `NBA_TEAMS: frozenset[str]`, `normalize_abbr(abbr: str) -> str`, `usage_pct(fga: float, fta: float, tov: float, minutes: float, team_minutes: float, team_fga: float, team_fta: float, team_tov: float) -> float`, `season_for_date(d: datetime.date) -> str` (e.g. `date(2026,11,5)` → `'2026-27'`, `date(2027,3,5)` → `'2026-27'`).

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_espn_mapping.py
"""Tests for shared ESPN↔NBA mapping helpers."""

from datetime import date

from tests.helpers import BaseTestCase


class TestEspnMapping(BaseTestCase):

    def test_normalize_abbr_maps_espn_aliases(self):
        from app.services.espn_mapping import normalize_abbr
        self.assertEqual(normalize_abbr('GS'), 'GSW')
        self.assertEqual(normalize_abbr('NO'), 'NOP')
        self.assertEqual(normalize_abbr('NY'), 'NYK')
        self.assertEqual(normalize_abbr('SA'), 'SAS')
        self.assertEqual(normalize_abbr('UTAH'), 'UTA')
        self.assertEqual(normalize_abbr('WSH'), 'WAS')
        self.assertEqual(normalize_abbr('BOS'), 'BOS')   # passthrough

    def test_nba_teams_is_the_30(self):
        from app.services.espn_mapping import NBA_TEAMS
        self.assertEqual(len(NBA_TEAMS), 30)
        self.assertIn('GSW', NBA_TEAMS)
        self.assertNotIn('GS', NBA_TEAMS)
        self.assertNotIn('STARS', NBA_TEAMS)

    def test_usage_pct_formula(self):
        from app.services.espn_mapping import usage_pct
        # LeBron fixture from test_hoopr_import: LAL totals min66 fga34 fta12 tov5
        expected = ((19 + 0.44 * 7 + 3) * (66 / 5)) / (36 * (34 + 0.44 * 12 + 5))
        self.assertAlmostEqual(
            usage_pct(19, 7, 3, 36, 66, 34, 12, 5), expected, places=9)

    def test_usage_pct_zero_minutes_is_zero(self):
        from app.services.espn_mapping import usage_pct
        self.assertEqual(usage_pct(1, 0, 0, 0, 66, 34, 12, 5), 0.0)

    def test_season_for_date(self):
        from app.services.espn_mapping import season_for_date
        self.assertEqual(season_for_date(date(2026, 11, 5)), '2026-27')
        self.assertEqual(season_for_date(date(2027, 3, 5)), '2026-27')
        self.assertEqual(season_for_date(date(2026, 7, 10)), '2025-26')

    def test_hoopr_import_still_exposes_behavior(self):
        # the CLI module must keep working after the extraction
        from app.cli.hoopr_import import _rows_from_player_box  # noqa: F401
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `source .venv/bin/activate && SECRET_KEY=test python -m unittest tests.test_espn_mapping -v`
Expected: FAIL/ERROR with `ModuleNotFoundError: No module named 'app.services.espn_mapping'`

- [ ] **Step 3: Write the module**

```python
# app/services/espn_mapping.py
"""Shared ESPN↔NBA mapping helpers.

HistoricalGameLog uses the ESPN id namespace (see app/cli/hoopr_import.py,
which imports these). Kept in services so both the CLI importer and the
game-day coordinator's history append share one source of truth.
"""

# ESPN abbreviations that differ from stats.nba.com convention.
ESPN_TO_NBA_ABBR = {
    'GS': 'GSW', 'NO': 'NOP', 'NY': 'NYK',
    'SA': 'SAS', 'UTAH': 'UTA', 'WSH': 'WAS',
}

NBA_TEAMS = frozenset({
    'ATL', 'BKN', 'BOS', 'CHA', 'CHI', 'CLE', 'DAL', 'DEN', 'DET', 'GSW',
    'HOU', 'IND', 'LAC', 'LAL', 'MEM', 'MIA', 'MIL', 'MIN', 'NOP', 'NYK',
    'OKC', 'ORL', 'PHI', 'PHX', 'POR', 'SAC', 'SAS', 'TOR', 'UTA', 'WAS',
})


def normalize_abbr(abbr: str) -> str:
    """Map an ESPN team abbreviation to NBA convention (passthrough if same)."""
    return ESPN_TO_NBA_ABBR.get(abbr, abbr)


def usage_pct(fga: float, fta: float, tov: float, minutes: float,
              team_minutes: float, team_fga: float, team_fta: float,
              team_tov: float) -> float:
    """Usage rate from box totals; 0.0 when the denominator degenerates."""
    denom = minutes * (team_fga + 0.44 * team_fta + team_tov)
    if denom <= 0:
        return 0.0
    return (fga + 0.44 * fta + tov) * (team_minutes / 5) / denom


def season_for_date(d) -> str:
    """NBA season string for a calendar date (seasons start in October)."""
    start_year = d.year if d.month >= 10 else d.year - 1
    return f"{start_year}-{str(start_year + 1)[-2:]}"
```

- [ ] **Step 4: Rewire `app/cli/hoopr_import.py`**

Replace the module-level `_ESPN_TO_NBA_ABBR = {...}` and `_NBA_TEAMS = frozenset({...})` definitions with:

```python
from app.services.espn_mapping import (
    ESPN_TO_NBA_ABBR as _ESPN_TO_NBA_ABBR,
    NBA_TEAMS as _NBA_TEAMS,
)
```

(Keep the private aliases so every existing usage and test in the file is untouched. The inline usage computation in `_rows_from_player_box` stays as-is — it is vectorized over team_totals and already tested; do NOT rewrite it to call `usage_pct`.)

- [ ] **Step 5: Run new tests + the two touched module suites**

Run: `source .venv/bin/activate && SECRET_KEY=test python -m unittest tests.test_espn_mapping tests.test_hoopr_import -v`
Expected: all PASS (6 new + 13 existing)

- [ ] **Step 6: Lint + commit**

```bash
ruff check . && bandit -q -r app -x tests -ll
git add app/services/espn_mapping.py app/cli/hoopr_import.py tests/test_espn_mapping.py
git commit -m "feat: extract shared ESPN mapping helpers into app/services/espn_mapping"
```

---

### Task 2: ESPN history append service

**Files:**
- Create: `app/services/espn_history_append.py`
- Test: `tests/test_espn_history_append.py`

**Interfaces:**
- Consumes (Task 1): `normalize_abbr`, `NBA_TEAMS`, `usage_pct`, `season_for_date` from `app.services.espn_mapping`; `_norm_player_id`, `_safe_float`, `_safe_str` from `app.cli.history_commands`.
- Produces (used by Task 3): `history_rows_exist(espn_game_id: str) -> bool`; `append_final_game(game: dict) -> int` where `game` is one `fetch_espn_scoreboard()` dict (keys: `espn_id`, `home`/`away` with `abbr`+`score`, `start_time` ISO str, `status`). Returns rows inserted (0 = skipped/already present/non-NBA). Raises nothing — logs and returns 0 on fetch/parse failure.

**ESPN summary JSON shape** (endpoint `https://site.api.espn.com/apis/site/v2/sports/basketball/nba/summary?event=<id>`): `data['boxscore']['players']` is a list of 2 team blocks; each has `team.abbreviation` and `statistics[0]` with `labels` (list like `['MIN','FG','3PT','FT','OREB','DREB','REB','AST','STL','BLK','TO','PF','+/-','PTS']`) and `athletes` (each: `athlete.id`, `athlete.displayName`, `starter` bool, `didNotPlay` bool, `stats` list of strings aligned to labels; empty `stats` list for DNP).

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_espn_history_append.py
"""Tests for ESPN summary → HistoricalGameLog append."""

from datetime import date
from unittest.mock import patch

from tests.helpers import BaseTestCase


def _summary_json():
    """Minimal 2-team, 2+1-player ESPN summary payload."""
    labels = ['MIN', 'FG', '3PT', 'FT', 'OREB', 'DREB', 'REB', 'AST',
              'STL', 'BLK', 'TO', 'PF', '+/-', 'PTS']

    def athlete(pid, name, starter, stats):
        return {'athlete': {'id': pid, 'displayName': name},
                'starter': starter, 'didNotPlay': not stats, 'stats': stats}

    return {'boxscore': {'players': [
        {'team': {'abbreviation': 'LAL'}, 'statistics': [{
            'labels': labels,
            'athletes': [
                athlete(1966, 'LeBron James', True,
                        ['36', '10-19', '2-6', '6-7', '1', '6', '7', '11',
                         '1', '0', '3', '2', '+12', '28']),
                athlete(6583, 'Anthony Davis', True,
                        ['30', '9-15', '0-1', '4-5', '3', '9', '12', '3',
                         '2', '3', '2', '3', '+8', '22']),
                athlete(999, 'Bench Guy', False, []),   # DNP
            ]}]},
        {'team': {'abbreviation': 'GS'}, 'statistics': [{   # ESPN alias
            'labels': labels,
            'athletes': [
                athlete(3975, 'Stephen Curry', True,
                        ['38', '12-24', '4-11', '5-5', '0', '4', '4', '5',
                         '2', '1', '2', '1', '-12', '33']),
            ]}]},
    ]}}


def _scoreboard_game(status='STATUS_FINAL'):
    return {
        'espn_id': '401800123',
        'home': {'abbr': 'LAL', 'score': 120}, 'away': {'abbr': 'GS', 'score': 110},
        'start_time': '2026-11-05T02:30Z', 'status': status,
    }


class TestAppendFinalGame(BaseTestCase):

    @patch('app.services.espn_history_append._fetch_summary')
    def test_appends_rows_with_mapped_fields(self, mock_fetch):
        from app.models import HistoricalGameLog
        from app.services.espn_history_append import append_final_game
        mock_fetch.return_value = _summary_json()
        with self.app.app_context():
            n = append_final_game(_scoreboard_game())
            self.assertEqual(n, 3)                      # DNP skipped
            lebron = HistoricalGameLog.query.filter_by(player_id='1966').one()
            self.assertEqual(lebron.sport, 'nba')
            self.assertEqual(lebron.game_id, '401800123')
            self.assertEqual(lebron.team_abbr, 'LAL')
            self.assertEqual(lebron.opp_abbr, 'GSW')     # alias normalized
            self.assertEqual(lebron.home_away, 'HOME')
            self.assertEqual(lebron.win_loss, 'W')       # 120 > 110
            self.assertEqual(lebron.season, '2026-27')   # from start_time date
            self.assertTrue(lebron.starter)
            s = lebron.stats
            self.assertEqual((s['pts'], s['reb'], s['ast']), (28.0, 7.0, 11.0))
            self.assertEqual((s['fgm'], s['fga'], s['fg3m'], s['fg3a']),
                             (10.0, 19.0, 2.0, 6.0))
            self.assertEqual((s['ftm'], s['fta'], s['stl'], s['blk'], s['tov']),
                             (6.0, 7.0, 1.0, 0.0, 3.0))
            self.assertEqual(s['minutes'], 36.0)
            self.assertEqual(s['plus_minus'], 12.0)
            # usage vs hand-computed LAL totals: min66 fga34 fta12 tov5
            expected = ((19 + 0.44 * 7 + 3) * (66 / 5)) / (36 * (34 + 0.44 * 12 + 5))
            self.assertAlmostEqual(s['usage_pct'], expected, places=6)
            curry = HistoricalGameLog.query.filter_by(player_id='3975').one()
            self.assertEqual(curry.team_abbr, 'GSW')
            self.assertEqual(curry.win_loss, 'L')
            self.assertEqual(curry.home_away, 'AWAY')

    @patch('app.services.espn_history_append._fetch_summary')
    def test_no_refetch_guard_skips_existing_game(self, mock_fetch):
        from app.services.espn_history_append import (
            append_final_game, history_rows_exist)
        mock_fetch.return_value = _summary_json()
        with self.app.app_context():
            self.assertFalse(history_rows_exist('401800123'))
            append_final_game(_scoreboard_game())
            self.assertTrue(history_rows_exist('401800123'))
            mock_fetch.reset_mock()
            n = append_final_game(_scoreboard_game())
            self.assertEqual(n, 0)
            mock_fetch.assert_not_called()               # guard fired BEFORE fetch

    @patch('app.services.espn_history_append._fetch_summary')
    def test_non_nba_team_skipped_entirely(self, mock_fetch):
        from app.services.espn_history_append import append_final_game
        payload = _summary_json()
        payload['boxscore']['players'][0]['team']['abbreviation'] = 'STARS'
        mock_fetch.return_value = payload
        game = _scoreboard_game()
        game['home']['abbr'] = 'STARS'
        with self.app.app_context():
            self.assertEqual(append_final_game(game), 0)

    @patch('app.services.espn_history_append._fetch_summary')
    def test_fetch_failure_returns_zero_not_raise(self, mock_fetch):
        from app.services.espn_history_append import append_final_game
        mock_fetch.side_effect = RuntimeError('espn down')
        with self.app.app_context():
            self.assertEqual(append_final_game(_scoreboard_game()), 0)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `source .venv/bin/activate && SECRET_KEY=test python -m unittest tests.test_espn_history_append -v`
Expected: ERROR `ModuleNotFoundError: No module named 'app.services.espn_history_append'`

- [ ] **Step 3: Write the module**

```python
# app/services/espn_history_append.py
"""Append a final game's player box score to HistoricalGameLog (ESPN source).

Same id namespace and mapping conventions as app/cli/hoopr_import.py.
No-refetch guard: if rows for the game already exist, no network call is
made. All failures log and return 0 — callers (the game-day coordinator)
retry naturally on their next tick.
"""

import logging
from datetime import datetime

import requests

from app import db
from app.cli.history_commands import _norm_player_id, _safe_float, _safe_str
from app.models import HistoricalGameLog
from app.services.espn_mapping import (
    NBA_TEAMS, normalize_abbr, season_for_date, usage_pct,
)
from app.utils.time_helpers import ET

logger = logging.getLogger(__name__)

ESPN_SUMMARY_URL = (
    'https://site.api.espn.com/apis/site/v2/sports/basketball/nba/summary')

# summary stat label → (stats-payload key, parser)
_SPLIT = lambda made_att, part: _safe_float(made_att.split('-')[part])  # noqa: E731


def history_rows_exist(espn_game_id: str) -> bool:
    return db.session.query(
        HistoricalGameLog.query.filter_by(
            sport='nba', game_id=str(espn_game_id)).exists()
    ).scalar()


def _fetch_summary(espn_id: str) -> dict:
    resp = requests.get(ESPN_SUMMARY_URL, params={'event': espn_id},
                        timeout=15)
    resp.raise_for_status()
    return resp.json()


def _player_records(payload: dict) -> list[dict]:
    """Flatten summary JSON to per-player dicts with raw float stats."""
    records = []
    for team_block in payload.get('boxscore', {}).get('players', []):
        abbr = normalize_abbr(
            _safe_str(team_block.get('team', {}).get('abbreviation')))
        stats_block = (team_block.get('statistics') or [{}])[0]
        labels = stats_block.get('labels') or []
        idx = {label: i for i, label in enumerate(labels)}

        def col(stats, label, default=0.0):
            i = idx.get(label)
            return _safe_float(stats[i]) if i is not None and i < len(stats) \
                else default

        for ath in stats_block.get('athletes', []):
            stats = ath.get('stats') or []
            if ath.get('didNotPlay') or not stats:
                continue
            fg = stats[idx['FG']] if 'FG' in idx else '0-0'
            fg3 = stats[idx['3PT']] if '3PT' in idx else '0-0'
            ft = stats[idx['FT']] if 'FT' in idx else '0-0'
            pm_raw = stats[idx['+/-']] if '+/-' in idx else '0'
            records.append({
                'player_id': _norm_player_id(ath.get('athlete', {}).get('id')),
                'player_name': _safe_str(
                    ath.get('athlete', {}).get('displayName')),
                'team_abbr': abbr,
                'starter': bool(ath.get('starter')),
                'minutes': col(stats, 'MIN'),
                'pts': col(stats, 'PTS'), 'reb': col(stats, 'REB'),
                'ast': col(stats, 'AST'), 'stl': col(stats, 'STL'),
                'blk': col(stats, 'BLK'), 'tov': col(stats, 'TO'),
                'fgm': _SPLIT(fg, 0), 'fga': _SPLIT(fg, 1),
                'fg3m': _SPLIT(fg3, 0), 'fg3a': _SPLIT(fg3, 1),
                'ftm': _SPLIT(ft, 0), 'fta': _SPLIT(ft, 1),
                'plus_minus': _safe_float(
                    str(pm_raw).replace('+', '') or None),
            })
    return records


def append_final_game(game: dict) -> int:
    """Insert HistoricalGameLog rows for one final scoreboard game dict."""
    espn_id = str(game.get('espn_id') or '')
    if not espn_id:
        return 0
    home_abbr = normalize_abbr(_safe_str(game.get('home', {}).get('abbr')))
    away_abbr = normalize_abbr(_safe_str(game.get('away', {}).get('abbr')))
    if home_abbr not in NBA_TEAMS or away_abbr not in NBA_TEAMS:
        logger.info("history-append: %s skipped (non-NBA teams %s/%s)",
                    espn_id, home_abbr, away_abbr)
        return 0
    if history_rows_exist(espn_id):
        return 0                                  # no-refetch guard

    try:
        payload = _fetch_summary(espn_id)
        records = _player_records(payload)
    except Exception as exc:
        logger.warning("history-append: %s fetch/parse failed: %s",
                       espn_id, exc)
        return 0
    if not records:
        return 0

    try:
        game_date = datetime.fromisoformat(
            game.get('start_time', '').replace('Z', '+00:00')
        ).astimezone(ET).date()
    except ValueError:
        logger.warning("history-append: %s bad start_time %r",
                       espn_id, game.get('start_time'))
        return 0
    season = season_for_date(game_date)
    home_score = int(game.get('home', {}).get('score') or 0)
    away_score = int(game.get('away', {}).get('score') or 0)

    totals = {}
    for rec in records:
        t = totals.setdefault(rec['team_abbr'],
                              {'minutes': 0.0, 'fga': 0.0, 'fta': 0.0,
                               'tov': 0.0})
        for key in t:
            t[key] += rec[key]

    rows = []
    for rec in records:
        team, is_home = rec['team_abbr'], rec['team_abbr'] == home_abbr
        won = (home_score > away_score) if is_home else \
              (away_score > home_score)
        t = totals[team]
        stats = {k: rec[k] for k in
                 ('pts', 'reb', 'ast', 'stl', 'blk', 'tov', 'fgm', 'fga',
                  'fg3m', 'fg3a', 'ftm', 'fta', 'minutes', 'plus_minus')}
        stats['usage_pct'] = usage_pct(
            rec['fga'], rec['fta'], rec['tov'], rec['minutes'],
            t['minutes'], t['fga'], t['fta'], t['tov'])
        rows.append(HistoricalGameLog(
            sport='nba', player_id=rec['player_id'],
            player_name=rec['player_name'], team_abbr=team,
            opp_abbr=away_abbr if is_home else home_abbr,
            game_id=espn_id, game_date=game_date, season=season,
            home_away='HOME' if is_home else 'AWAY',
            win_loss='W' if won else 'L',
            starter=rec['starter'], stats=stats,
        ))
    db.session.add_all(rows)
    db.session.commit()
    logger.info("history-append: %s +%d rows", espn_id, len(rows))
    return len(rows)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `source .venv/bin/activate && SECRET_KEY=test python -m unittest tests.test_espn_history_append -v`
Expected: 4 PASS

- [ ] **Step 5: Lint + commit**

```bash
ruff check . && bandit -q -r app -x tests -ll
git add app/services/espn_history_append.py tests/test_espn_history_append.py
git commit -m "feat: ESPN summary → HistoricalGameLog append with no-refetch guard"
```

---

### Task 3: Coordinator tick state machine

**Files:**
- Create: `app/services/game_day_coordinator.py`
- Test: `tests/test_game_day_coordinator.py`

**Interfaces:**
- Consumes: `fetch_espn_scoreboard(date_str=None)`, `_STATUS_FINAL` from `app.services.nba_service`; `resolve_and_grade` from `app.services.scheduler` (idempotent: grades ALL pending bets, postmortems, finalizes today's final snapshots); `append_final_game`, `history_rows_exist` from Task 2; `GameSnapshot`, `Bet`, `JobLog` models; `Outcome` from `app.enums`.
- Produces (used by Task 4): `run_tick(now: datetime | None = None) -> str` returning the tier acted in: `'dormant' | 'pre-game' | 'live' | 'post'`. Module-level `_DAY_CACHE: dict` (test hook: clear between tests). `LOOKBACK_DAYS = 3`, `PREGAME_LEAD_MINUTES = 30`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_game_day_coordinator.py
"""Tests for the game-day coordinator tick state machine."""

from datetime import datetime, date
from unittest.mock import patch, call
from zoneinfo import ZoneInfo

from tests.helpers import BaseTestCase

ET = ZoneInfo("America/New_York")


def _game(espn_id='401800123', status='STATUS_FINAL', tip_et_hour=19):
    # start_time is UTC in ESPN payloads
    return {
        'espn_id': espn_id, 'status': status,
        'home': {'abbr': 'LAL', 'score': 120},
        'away': {'abbr': 'GS', 'score': 110},
        'start_time': f'2026-11-06T{tip_et_hour + 5:02d}:00Z',  # ET+5 in Nov
    }


NOW_EVENING = datetime(2026, 11, 6, 22, 0, tzinfo=ET)   # 10 PM ET game night
NOW_MORNING = datetime(2026, 11, 6, 9, 0, tzinfo=ET)


class CoordinatorBase(BaseTestCase):
    def setUp(self):
        super().setUp()
        from app.services import game_day_coordinator as gdc
        gdc._DAY_CACHE.clear()


@patch('app.services.game_day_coordinator.resolve_and_grade')
@patch('app.services.game_day_coordinator.append_final_game')
@patch('app.services.game_day_coordinator.fetch_espn_scoreboard')
class TestTiers(CoordinatorBase):

    def test_no_games_day_is_dormant_and_caches(self, mock_sb, mock_app, mock_rag):
        from app.services.game_day_coordinator import run_tick
        mock_sb.return_value = []
        with self.app.app_context():
            self.assertEqual(run_tick(now=NOW_MORNING), 'dormant')
            first_calls = mock_sb.call_count      # today + 3 lookback dates
            self.assertEqual(run_tick(now=NOW_MORNING), 'dormant')
            self.assertEqual(mock_sb.call_count, first_calls)   # zero network
        mock_rag.assert_not_called()

    def test_pregame_before_first_tip_minus_lead(self, mock_sb, mock_app, mock_rag):
        from app.services.game_day_coordinator import run_tick
        mock_sb.return_value = [_game(status='STATUS_SCHEDULED', tip_et_hour=19)]
        with self.app.app_context():
            # 9 AM, tip 7 PM → pre-game, and no second scoreboard fetch
            self.assertEqual(run_tick(now=NOW_MORNING), 'pre-game')

    def test_live_window_fetches_fresh_and_detects_final(self, mock_sb, mock_app, mock_rag):
        from app.services.game_day_coordinator import run_tick
        mock_sb.return_value = [_game(status='STATUS_FINAL')]
        mock_app.return_value = 25
        with self.app.app_context():
            tier = run_tick(now=NOW_EVENING)
        self.assertEqual(tier, 'live')
        mock_app.assert_called_once()             # chain fired for the game
        mock_rag.assert_called_once()

    def test_post_when_all_final_and_nothing_needed(self, mock_sb, mock_app, mock_rag):
        from app.services import game_day_coordinator as gdc
        mock_sb.return_value = [_game(status='STATUS_FINAL')]
        mock_app.return_value = 0
        with self.app.app_context():
            with patch.object(gdc, 'history_rows_exist', return_value=True):
                gdc.run_tick(now=NOW_EVENING)          # first: live pass
                tier = gdc.run_tick(now=NOW_EVENING)   # nothing left → post
                self.assertEqual(tier, 'post')
                sb_calls = gdc.fetch_espn_scoreboard.call_count
                gdc.run_tick(now=NOW_EVENING)          # done-cached → dormant
                self.assertEqual(
                    gdc.fetch_espn_scoreboard.call_count, sb_calls)


@patch('app.services.game_day_coordinator.resolve_and_grade')
@patch('app.services.game_day_coordinator.append_final_game')
@patch('app.services.game_day_coordinator.fetch_espn_scoreboard')
class TestChainAndCatchUp(CoordinatorBase):

    def test_chain_skips_resolve_when_no_pending_and_no_snapshot_diff(
            self, mock_sb, mock_app, mock_rag):
        # final game whose history is missing but bets/snapshots agree:
        # append fires, resolve_and_grade does NOT
        from app.services.game_day_coordinator import run_tick
        mock_sb.return_value = [_game()]
        mock_app.return_value = 25
        with self.app.app_context():
            run_tick(now=NOW_EVENING)
        mock_app.assert_called_once()
        mock_rag.assert_not_called()

    def test_pending_bets_trigger_resolve(self, mock_sb, mock_app, mock_rag):
        from app import db
        from app.models import Bet
        from app.services.game_day_coordinator import run_tick
        mock_sb.return_value = [_game()]
        mock_app.return_value = 0
        with self.app.app_context():
            db.session.add(Bet(
                match='LAL vs GSW', match_date=datetime(2026, 11, 6, 19, 0),
                bet_type='total', selection='over', odds=-110, stake=10.0,
                outcome='pending'))
            db.session.commit()
            with patch('app.services.game_day_coordinator.history_rows_exist',
                       return_value=True):
                run_tick(now=NOW_EVENING)
        mock_rag.assert_called_once()

    def test_unfinalized_snapshot_triggers_resolve(self, mock_sb, mock_app, mock_rag):
        from app import db
        from app.models import GameSnapshot
        from app.services.game_day_coordinator import run_tick
        mock_sb.return_value = [_game()]
        mock_app.return_value = 0
        with self.app.app_context():
            db.session.add(GameSnapshot(
                espn_id='401800123', game_date=date(2026, 11, 6),
                home_team='Lakers', away_team='Warriors', is_final=False))
            db.session.commit()
            with patch('app.services.game_day_coordinator.history_rows_exist',
                       return_value=True):
                run_tick(now=NOW_EVENING)
        mock_rag.assert_called_once()

    def test_lookback_appends_missed_games(self, mock_sb, mock_app, mock_rag):
        # day-cache init scans LOOKBACK_DAYS past dates via date_str param
        from app.services.game_day_coordinator import run_tick, LOOKBACK_DAYS
        past_final = _game(espn_id='401800000')
        mock_sb.side_effect = lambda date_str=None: (
            [] if date_str is None else [past_final])
        mock_app.return_value = 20
        with self.app.app_context():
            run_tick(now=NOW_MORNING)
        dated = [c for c in mock_sb.call_args_list
                 if c.kwargs.get('date_str') or (c.args and c.args[0])]
        self.assertEqual(len(dated), LOOKBACK_DAYS)
        self.assertEqual(mock_app.call_count, LOOKBACK_DAYS)  # per past final

    def test_joblog_written_for_chain(self, mock_sb, mock_app, mock_rag):
        from app.models import JobLog
        from app.services.game_day_coordinator import run_tick
        mock_sb.return_value = [_game()]
        mock_app.return_value = 25
        with self.app.app_context():
            run_tick(now=NOW_EVENING)
            job = JobLog.query.filter_by(job_name='game-final-chain').one()
            self.assertEqual(job.status, 'success')
            self.assertIn('401800123', job.message)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `source .venv/bin/activate && SECRET_KEY=test python -m unittest tests.test_game_day_coordinator -v`
Expected: ERROR `ModuleNotFoundError: No module named 'app.services.game_day_coordinator'`

(If `Bet`/`GameSnapshot` constructor kwargs above don't match the real models, adjust the TEST fixtures to the models — check `app/models.py` — not the other way around.)

- [ ] **Step 3: Write the module**

```python
# app/services/game_day_coordinator.py
"""Game-day coordinator: tiered polling + event chains (Plan A2).

One APScheduler job ticks run_tick() every 5 minutes. Each tick is a
state-reconciliation pass — everything derives from the DB vs the ESPN
scoreboard, never from "what time is it", so the first tick after any
downtime self-heals (sleeping-laptop reality).

Tiers per ET day: DORMANT (no games / day complete) → PRE-GAME (before
first tip − 30 min) → LIVE (scoreboard each tick) → POST (all final,
chains done). The day verdict is memoized in-process; a restart simply
re-checks once.

Caching hard rule (spec): LIVE reads use nba_service.fetch_espn_scoreboard
directly (uncached) — never a cache with TTL ≥ the tick interval.
"""

import logging
from datetime import datetime, timedelta, timezone

from app import db
from app.enums import Outcome
from app.models import Bet, GameSnapshot, JobLog
from app.services.espn_history_append import (
    append_final_game, history_rows_exist,
)
from app.services.nba_service import _STATUS_FINAL, fetch_espn_scoreboard
from app.services.scheduler import resolve_and_grade
from app.utils.time_helpers import ET

logger = logging.getLogger(__name__)

LOOKBACK_DAYS = 3
PREGAME_LEAD_MINUTES = 30

# {et_date: {'has_games': bool, 'first_tip': datetime|None, 'done': bool}}
_DAY_CACHE: dict = {}


def _first_tip(games) -> datetime | None:
    tips = []
    for g in games:
        try:
            tips.append(datetime.fromisoformat(
                g.get('start_time', '').replace('Z', '+00:00')
            ).astimezone(ET))
        except ValueError:
            continue
    return min(tips) if tips else None


def _catch_up_lookback(today) -> int:
    """Append history for final games on the previous LOOKBACK_DAYS dates."""
    appended = 0
    for delta in range(1, LOOKBACK_DAYS + 1):
        day = today - timedelta(days=delta)
        for game in fetch_espn_scoreboard(day.strftime('%Y%m%d')):
            if game.get('status') == _STATUS_FINAL:
                appended += 1 if append_final_game(game) else 0
    return appended


def _needs_resolve(final_games) -> bool:
    """True when the DB disagrees with a final scoreboard on bets/snapshots."""
    if Bet.query.filter_by(outcome=Outcome.PENDING.value).count():
        return True
    for g in final_games:
        snap = GameSnapshot.query.filter_by(
            espn_id=str(g.get('espn_id'))).first()
        if snap is not None and not snap.is_final:
            return True
    return False


def _run_chain(final_games) -> None:
    """Grade/postmortem/finalize (existing idempotent job) + history append."""
    chained_ids, steps = [], []
    if _needs_resolve(final_games):
        resolve_and_grade()
        steps.append('resolve_and_grade')
    for g in final_games:
        espn_id = str(g.get('espn_id'))
        if not history_rows_exist(espn_id):
            inserted = append_final_game(g)
            if inserted:
                chained_ids.append(espn_id)
                steps.append(f'history+{inserted}')
    if steps:
        job = JobLog(job_name='game-final-chain',
                     started_at=datetime.now(timezone.utc),
                     finished_at=datetime.now(timezone.utc),
                     status='success',
                     message=f"games={','.join(chained_ids) or '-'} "
                             f"steps={';'.join(steps)}")
        db.session.add(job)
        db.session.commit()


def run_tick(now: datetime | None = None) -> str:
    """One coordinator pass; returns the tier it acted in."""
    now = now or datetime.now(ET)
    today = now.date()

    state = _DAY_CACHE.get(today)
    if state is None:
        games = fetch_espn_scoreboard()
        state = {'has_games': bool(games),
                 'first_tip': _first_tip(games), 'done': False}
        _DAY_CACHE.clear()
        _DAY_CACHE[today] = state
        appended = _catch_up_lookback(today)
        if appended:
            logger.info("coordinator: lookback appended %d games", appended)

    if not state['has_games'] or state['done']:
        return 'dormant'

    if state['first_tip'] and now < state['first_tip'] - timedelta(
            minutes=PREGAME_LEAD_MINUTES):
        return 'pre-game'

    games = fetch_espn_scoreboard()          # fresh LIVE read (uncached)
    final_games = [g for g in games if g.get('status') == _STATUS_FINAL]
    pending_work = [
        g for g in final_games
        if not history_rows_exist(str(g.get('espn_id')))
    ]
    if pending_work or _needs_resolve(final_games):
        _run_chain(final_games)
        return 'live'

    if games and len(final_games) == len(games):
        state['done'] = True
        return 'post'
    return 'live'
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `source .venv/bin/activate && SECRET_KEY=test python -m unittest tests.test_game_day_coordinator -v`
Expected: 9 PASS

- [ ] **Step 5: Lint + commit**

```bash
ruff check . && bandit -q -r app -x tests -ll
git add app/services/game_day_coordinator.py tests/test_game_day_coordinator.py
git commit -m "feat: game-day coordinator tick state machine with catch-up"
```

---

### Task 4: Wiring — scheduler job, odds guard, CLI, weekly reconciliation

**Files:**
- Modify: `app/services/scheduler.py` (register 2 jobs; guard in `snapshot_props_odds`)
- Modify: `app/cli/hoopr_import.py` (extract `import_hoopr_seasons()` callable)
- Create: `app/cli/coordinator_commands.py`
- Modify: `app/cli/__init__.py` (register new CLI)
- Test: additions to `tests/test_game_day_coordinator.py` and `tests/test_hoopr_import.py`

**Interfaces:**
- Consumes: `run_tick` (Task 3); the click command internals of `cli_import_hoopr_logs`.
- Produces: scheduler job ids `game_day_coordinator` (CronTrigger `minute='*/5'`, ET) and `hoopr_reconcile` (Sun 08:20 ET, current season); `import_hoopr_seasons(sport='nba', seasons=1, season_type='Regular Season', from_dir=None, max_games=None) -> dict` (the CLI body, minus click echo — returns `{'inserted': int, 'skipped': int, 'errors': list[str]}`); `flask coordinator-tick` CLI printing the tier.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_game_day_coordinator.py`:

```python
class TestWiring(CoordinatorBase):

    @patch('app.services.game_day_coordinator.run_tick', return_value='dormant')
    def test_coordinator_tick_cli(self, mock_tick):
        runner = self.app.test_cli_runner()
        result = runner.invoke(args=['coordinator-tick'])
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIn('dormant', result.output)
        mock_tick.assert_called_once()

    @patch('app.services.game_day_coordinator.fetch_espn_scoreboard',
           return_value=[])
    def test_snapshot_props_odds_skips_on_empty_day(self, mock_sb):
        from app.services import scheduler as sched
        with patch.object(sched, '_capture_todays_snapshots') as mock_cap:
            with self.app.app_context():
                sched.snapshot_props_odds()
            mock_cap.assert_not_called()
```

Append to `tests/test_hoopr_import.py`:

```python
class TestImportCallable(BaseTestCase):

    @patch('app.cli.hoopr_import._load_player_box_df')
    def test_import_hoopr_seasons_returns_counts(self, mock_load):
        from app.cli.hoopr_import import import_hoopr_seasons
        mock_load.return_value = _player_box_df()
        with self.app.app_context():
            result = import_hoopr_seasons(seasons=1)
        self.assertEqual(result['inserted'], 4)
        self.assertEqual(result['errors'], [])
```

- [ ] **Step 2: Run to verify failures**

Run: `source .venv/bin/activate && SECRET_KEY=test python -m unittest tests.test_game_day_coordinator.TestWiring tests.test_hoopr_import.TestImportCallable -v`
Expected: FAIL/ERROR (`coordinator-tick` unknown command; `import_hoopr_seasons` missing; guard not implemented)

- [ ] **Step 3: Extract `import_hoopr_seasons` in `app/cli/hoopr_import.py`**

Move the body of `cli_import_hoopr_logs` (from the `JobLog(...)` line through the `finally:` block) into:

```python
def import_hoopr_seasons(sport='nba', seasons=3,
                         season_type='Regular Season', from_dir=None,
                         max_games=None) -> dict:
    """Callable core of import-hoopr-logs (scheduler + CLI entry points)."""
    season_type_code = _SEASON_TYPE_CODES[season_type]
    # ... existing body unchanged, except:
    #   - replace every `click.echo(...)` with `logger.info(...)`
    #     EXCEPT keep the season-summary echo as logger.info too
    #   - at the end (after finally), add:  return {'inserted': inserted,
    #     'skipped': skipped, 'errors': errors}
```

Then shrink the click command to:

```python
@click.command('import-hoopr-logs')
@click.option('--sport', default='nba', show_default=True)
@click.option('--seasons', default=3, show_default=True, type=int)
@click.option('--season-type', default='Regular Season', show_default=True,
              type=click.Choice(sorted(_SEASON_TYPE_CODES)))
@click.option('--from-dir', default=None)
@click.option('--max-games', default=None, type=int)
def cli_import_hoopr_logs(sport, seasons, season_type, from_dir, max_games):
    """Backfill HistoricalGameLog from hoopR (ESPN) data dumps on GitHub."""
    if sport != 'nba':
        raise click.BadParameter(
            f"sport '{sport}' not supported yet (nba only; mlb/nfl are "
            "Phase 3/4)")
    result = import_hoopr_seasons(sport=sport, seasons=seasons,
                                  season_type=season_type, from_dir=from_dir,
                                  max_games=max_games)
    click.echo(f"Done: inserted={result['inserted']} "
               f"skipped={result['skipped']}"
               + (f" errors={'; '.join(result['errors'])}"
                  if result['errors'] else ""))
```

Keep the sport guard in the CLI; `import_hoopr_seasons` itself may assume 'nba'. Existing tests assert on JobLog + row counts and the CLI exit codes — run them; if an existing test asserted on a removed echo line, update THAT assertion to the new `Done:` format.

- [ ] **Step 4: Create `app/cli/coordinator_commands.py`**

```python
"""Manual CLI entry point for the game-day coordinator (off-season testing)."""

import click


@click.command('coordinator-tick')
def cli_coordinator_tick():
    """Run one coordinator tick and print the tier it acted in."""
    from app.services.game_day_coordinator import run_tick
    click.echo(f"tier: {run_tick()}")


def register_coordinator_commands(app):
    app.cli.add_command(cli_coordinator_tick)
```

In `app/cli/__init__.py:register_cli`, add alongside the existing pairs:

```python
    from app.cli.coordinator_commands import register_coordinator_commands
```
and
```python
    register_coordinator_commands(app)
```

- [ ] **Step 5: Guard + jobs in `app/services/scheduler.py`**

At the top of `snapshot_props_odds()` (line ~1155), add:

```python
def snapshot_props_odds():
    """Capture props/odds snapshots (guarded: skip when no games today)."""
    from app.services.game_day_coordinator import fetch_espn_scoreboard
    if not fetch_espn_scoreboard():
        logger.info("snapshot_props_odds: no games today — skipped")
        return
    _capture_todays_snapshots(prefetch_props=True)
```

(Import via the coordinator module so tests patching `game_day_coordinator.fetch_espn_scoreboard` also govern the guard — one patch point. Adjust to the real body: the existing function calls `_capture_todays_snapshots`; keep whatever it currently does after the guard.)

In `init_scheduler(app)`, before `scheduler.start()`:

```python
    # Plan A2: game-day coordinator — tiered polling + event chains
    scheduler.add_job(
        lambda: _log_job('game_day_coordinator', _run_coordinator_tick),
        CronTrigger(minute='*/5', timezone=APP_TIMEZONE),
        id='game_day_coordinator',
        replace_existing=True,
    )

    # Plan A2: weekly hoopR reconciliation (current season, idempotent)
    scheduler.add_job(
        lambda: _log_job('hoopr_reconcile', _run_hoopr_reconcile),
        CronTrigger(day_of_week='sun', hour=8, minute=20,
                    timezone=APP_TIMEZONE),
        id='hoopr_reconcile',
        replace_existing=True,
    )
```

with module-level functions next to the other job functions:

```python
def _run_coordinator_tick():
    from app.services.game_day_coordinator import run_tick
    app = _get_app()
    with app.app_context():
        tier = run_tick()
        logger.info("coordinator tick: %s", tier)


def _run_hoopr_reconcile():
    from app.cli.hoopr_import import import_hoopr_seasons
    app = _get_app()
    with app.app_context():
        result = import_hoopr_seasons(seasons=1)
        logger.info("hoopr reconcile: %s", result)
```

- [ ] **Step 6: Run the new tests, then both touched suites**

Run: `source .venv/bin/activate && SECRET_KEY=test python -m unittest tests.test_game_day_coordinator tests.test_hoopr_import -v`
Expected: all PASS (existing + new)

- [ ] **Step 7: Lint + commit**

```bash
ruff check . && bandit -q -r app -x tests -ll
git add app/services/scheduler.py app/cli/hoopr_import.py \
        app/cli/coordinator_commands.py app/cli/__init__.py \
        tests/test_game_day_coordinator.py tests/test_hoopr_import.py
git commit -m "feat: wire coordinator + weekly hoopR reconcile into scheduler; games-today guard on odds snapshots"
```

---

### Task 5: Full verification + docs

**Files:**
- Modify: `CLAUDE.md` (scheduler job count line)
- Modify: `docs/superpowers/specs/2026-07-10-plan-a2-game-day-coordinator-design.md` (status line only)

**Interfaces:** none — verification task.

- [ ] **Step 1: Full suite, foreground**

Run: `source .venv/bin/activate && SECRET_KEY=test python -m coverage run -m unittest discover -s tests`
Expected: `OK`, count ≥ 1003 + new tests (≈1018+). Then `python -m coverage report --include="app/*"` — total ≥ 80%.

- [ ] **Step 2: Lint**

Run: `ruff check . && bandit -q -r app -x tests -ll`
Expected: clean.

- [ ] **Step 3: Manual smoke — CLI tick against the real DB (off-season = dormant)**

Run (from repo root): `source .venv/bin/activate && flask --app run.py coordinator-tick`
Expected: `tier: dormant` (July — no NBA games; also proves ESPN reachability + scheduler guard keeps jobs off). This is the ONLY step that touches the network.

- [ ] **Step 4: Update docs**

In `CLAUDE.md`, change the line `- Scheduler has 17 registered jobs as of 2026-06-26` to `- Scheduler has 19 registered jobs as of 2026-07-10 (game_day_coordinator + hoopr_reconcile added in Plan A2)`.
In the spec header, change `Status: approved by user (Approach A of 3)` to `Status: implemented (this plan)`.

- [ ] **Step 5: Commit**

```bash
git add CLAUDE.md docs/superpowers/specs/2026-07-10-plan-a2-game-day-coordinator-design.md
git commit -m "docs: Plan A2 implemented — job count + spec status"
```

---

## Self-Review (performed at write time)

- **Spec coverage**: tiered polling (T3 tiers), event chain (T3 `_run_chain` reusing `resolve_and_grade` for grade/postmortem/finalize + T2 append), no-refetch (T2 guard, asserted before-fetch), nightly currency (chain appends same-night; T4 weekly reconcile), catch-up (T3 lookback + state-driven detection), caching policy (T3 uses uncached `fetch_espn_scoreboard`; day memo; persistence-as-cache = T2 guard), safety nets kept (no removals anywhere), odds guard (T4), manual CLI (T4), ET everywhere, ESPN namespace (T1/T2).
- **Placeholder scan**: clean — every step has runnable code/commands. Two intentional adjust-to-reality notes (model kwargs in T3 fixtures, existing echo assertions in T4) direct the implementer to defer to existing code, not to invent.
- **Type consistency**: `append_final_game(game: dict) -> int` and `history_rows_exist(str) -> bool` used identically in T2/T3; `run_tick(now=None) -> str` tiers `dormant|pre-game|live|post` consistent across T3/T4; `import_hoopr_seasons(...) -> dict` keys match T4 test.
