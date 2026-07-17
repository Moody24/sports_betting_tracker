# Plan C Increment 2 — Scenario Signal Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Wire the Plan B ScenarioSplit engine into live prop scoring as a display + bounded-tier-nudge signal (`agreement_score`), behind `USE_SCENARIO_SIGNAL` (default false).

**Architecture:** Four units — a name→ESPN-id crosswalk resolver cached from ScenarioSplit itself; a `ScenarioContextPack` row persisted atomically with each splits refresh (quantile edges + team tier/pace maps); a live-context builder that emits only pre-game-knowable dimensions using bucketing logic shared with the historical builder; and a flag-gated block in `ValueDetector.score_prop` that surfaces the score and nudges `confidence_tier` one step. Spec: `docs/superpowers/specs/2026-07-17-plan-c-increment-2-scenario-signal-design.md`.

**Tech Stack:** Flask + SQLAlchemy, pandas (existing scenario modules), unittest (NOT pytest), alembic via Flask-Migrate.

## Global Constraints

- Branch `plan-c-increment-2-scenario-signal` off main (`643aac6` or later). No merge, no push, no writes to `instance/app.db`.
- Test runner is **unittest**: `SECRET_KEY=test python -m coverage run -m unittest discover -s tests` (FOREGROUND only, never backgrounded). Coverage gate ≥ 80% (`python -m coverage report --include="app/*"`). `ruff check .` and `bandit -q -r app -x tests -ll` clean before every commit.
- Conventional commits, **no Co-Authored-By**.
- `USE_SCENARIO_SIGNAL` defaults false; flag-off must be byte-identical to today (regression-tested).
- Constants (exact values from spec): `MIN_MATCHES = 5`, `STRONG_THRESHOLD = 0.5`, `MAX_PACK_AGE_DAYS = 7`, spread "big" threshold `7.0` (existing behavior).
- Bucket labels are the STORED values (what `refresh_splits` writes from `ctx_*` columns): `home_away` → `'home'/'away'` (lowercase, from HistoricalGameLog.home_away — the `DIMENSIONS` dict's `('HOME','AWAY')` tuple is NOT what's stored), `rest_bucket` → `'0'/'1'/'2'/'3+'`, `role` → `'starter'/'bench'`, `season_segment` → `'early'/'mid'/'late'`, `opp_def_tier` → `'top10'/'mid'/'bottom10'`, `pace_tier` → `'slow'/'mid'/'fast'`, `fav_dog` → `'fav_big'/'fav'/'dog'/'dog_big'`, `total_bucket` → `'low'/'mid'/'high'`.
- Scenario stats: `SPLIT_STATS = ('pts','reb','ast','fg3m','pra')` — props map via `PROP_TO_SPLIT_STAT`; steals/blocks have NO signal (absent, not zero).
- ET timezone (`ZoneInfo("America/New_York")`) for "today" logic, as everywhere in this codebase.

---

### Task 1: `ScenarioContextPack` model + migration

**Files:**
- Modify: `app/models.py` (append after `ScenarioSplit`, ~line 540)
- Create: migration via `flask db migrate` (autogenerate) — see Step 4's local quirk
- Test: `tests/test_models.py` (append)

**Interfaces:**
- Produces: `ScenarioContextPack(sport: str, payload: str (JSON), computed_at: datetime)` — one live row per sport; later tasks query `ScenarioContextPack.query.filter_by(sport='nba').first()`.

- [ ] **Step 1: Write the failing test** (append to `tests/test_models.py`):

```python
class TestScenarioContextPack(BaseTestCase):

    def test_pack_round_trips_payload_json(self):
        import json
        from datetime import datetime, timezone
        from app.models import ScenarioContextPack
        with self.app.app_context():
            db.session.add(ScenarioContextPack(
                sport='nba',
                payload=json.dumps({'season': '2025-26', 'total_bins': [200.0, 220.0, 230.0, 260.0]}),
                computed_at=datetime.now(timezone.utc),
            ))
            db.session.commit()
            row = ScenarioContextPack.query.filter_by(sport='nba').first()
            self.assertEqual(json.loads(row.payload)['season'], '2025-26')
```

- [ ] **Step 2: Run it, expect FAIL** (`ImportError: cannot import name 'ScenarioContextPack'`):

`SECRET_KEY=test python -m unittest tests.test_models.TestScenarioContextPack -v`

- [ ] **Step 3: Add the model** in `app/models.py` directly after the `ScenarioSplit` class:

```python
class ScenarioContextPack(db.Model):
    """Live-context lookup pack persisted atomically with each splits refresh.

    Derived data (quantile bin edges + team tier maps) — one live row per
    sport, replaced wholesale by the scenario engine.
    """

    id = db.Column(db.Integer, primary_key=True)
    sport = db.Column(db.String(10), nullable=False, unique=True, default='nba')
    payload = db.Column(db.Text, nullable=False)   # JSON
    computed_at = db.Column(
        db.DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))
```

- [ ] **Step 4: Generate the migration.** The `flask db` CLI is broken locally (Flask-Migrate g-context); generate from Python instead, in the worktree with a scratch DB:

```bash
SECRET_KEY=test DATABASE_URL=sqlite:///$(pwd)/.mig-scratch.db python -c "
from app import create_app
from flask_migrate import upgrade, migrate as mig
app = create_app()
with app.app_context():
    upgrade()
    mig(message='scenario context pack')
"
rm -f .mig-scratch.db
```

Inspect the generated file in `migrations/versions/` — it must create exactly the `scenario_context_pack` table (id, sport unique, payload, computed_at) and nothing else. If autogenerate emits unrelated drift, delete those ops.

- [ ] **Step 5: Run test, expect PASS** (BaseTestCase uses `create_all`, no migration needed at test time):

`SECRET_KEY=test python -m unittest tests.test_models.TestScenarioContextPack -v`

- [ ] **Step 6: Commit**

```bash
git add app/models.py migrations/versions/ tests/test_models.py
git commit -m "feat: ScenarioContextPack model + migration"
```

---

### Task 2: Shared fixed-logic bucket helpers in `scenario_dimensions.py`

**Files:**
- Modify: `app/services/scenario_dimensions.py`
- Test: `tests/test_scenario_dimensions.py` (append)

**Interfaces:**
- Produces (module-level in `scenario_dimensions.py`):
  - `REST_BINS = [-1, 0, 1, 2, 10_000]`, `REST_LABELS = ('0', '1', '2', '3+')`
  - `SEGMENT_BINS = [9, 12, 14, 17]`, `SEGMENT_LABELS = ('early', 'mid', 'late')`
  - `SPREAD_BIG = 7.0`
  - `rest_bucket_label(days_rest: float) -> str`
  - `season_segment_label(d: date) -> str | None` (None outside Oct–Apr)
  - `fav_dog_label(spread: float, team_is_favored: bool) -> str`
- Consumes: nothing new. `build_context` is refactored to use the same constants/function so historical and live bucketing share one source of truth.

- [ ] **Step 1: Write the failing tests** (append to `tests/test_scenario_dimensions.py`):

```python
class TestSharedBucketHelpers(BaseTestCase):

    def test_rest_bucket_label_boundaries(self):
        from app.services.scenario_dimensions import rest_bucket_label
        self.assertEqual(rest_bucket_label(0), '0')
        self.assertEqual(rest_bucket_label(1), '1')
        self.assertEqual(rest_bucket_label(2), '2')
        self.assertEqual(rest_bucket_label(3), '3+')
        self.assertEqual(rest_bucket_label(99), '3+')   # first-game convention

    def test_season_segment_label_month_edges(self):
        from datetime import date
        from app.services.scenario_dimensions import season_segment_label
        self.assertEqual(season_segment_label(date(2025, 10, 25)), 'early')
        self.assertEqual(season_segment_label(date(2025, 12, 31)), 'early')
        self.assertEqual(season_segment_label(date(2026, 1, 1)), 'mid')
        self.assertEqual(season_segment_label(date(2026, 2, 28)), 'mid')
        self.assertEqual(season_segment_label(date(2026, 3, 1)), 'late')
        self.assertEqual(season_segment_label(date(2026, 4, 12)), 'late')

    def test_fav_dog_label_matches_historical_rules(self):
        from app.services.scenario_dimensions import fav_dog_label
        self.assertEqual(fav_dog_label(9.5, True), 'fav_big')
        self.assertEqual(fav_dog_label(7.0, True), 'fav')       # big is strictly > 7
        self.assertEqual(fav_dog_label(3.0, False), 'dog')
        self.assertEqual(fav_dog_label(10.0, False), 'dog_big')
        self.assertEqual(fav_dog_label(0.0, False), 'fav')      # pick'em convention
```

- [ ] **Step 2: Run, expect FAIL** (ImportError):

`SECRET_KEY=test python -m unittest tests.test_scenario_dimensions.TestSharedBucketHelpers -v`

- [ ] **Step 3: Implement.** In `app/services/scenario_dimensions.py`, add below `DIMENSIONS`:

```python
# Fixed-logic bucketing shared by the historical builder (build_context) and
# the live builder (live_context). One source of truth: edit here, both move.
REST_BINS = [-1, 0, 1, 2, 10_000]
REST_LABELS = ('0', '1', '2', '3+')
SEGMENT_BINS = [9, 12, 14, 17]          # shifted months: Oct..Apr -> 10..16
SEGMENT_LABELS = ('early', 'mid', 'late')
SPREAD_BIG = 7.0


def rest_bucket_label(days_rest: float) -> str:
    for edge, label in zip(REST_BINS[1:], REST_LABELS):
        if days_rest <= edge:
            return label
    return REST_LABELS[-1]


def season_segment_label(d) -> str | None:
    month = d.month
    shifted = month if month >= 9 else month + 12
    for edge, label in zip(SEGMENT_BINS[1:], SEGMENT_LABELS):
        if shifted <= edge:
            return label
    return None


def fav_dog_label(spread: float, team_is_favored: bool) -> str:
    if spread == 0:
        return 'fav'
    big = abs(spread) > SPREAD_BIG
    if team_is_favored:
        return 'fav_big' if big else 'fav'
    return 'dog_big' if big else 'dog'
```

Then refactor `build_context` to consume the SAME constants (behavior-neutral):
- rest: `pd.cut(rest.fillna(99), bins=REST_BINS, labels=list(REST_LABELS))`
- segment: `pd.cut(month.where(month >= 9, month + 12), bins=SEGMENT_BINS, labels=list(SEGMENT_LABELS))`
- `_fav_bucket`'s body becomes: `return fav_dog_label(row['spread'], (row['favored'] == 'home') == row['is_home_side'])`

- [ ] **Step 4: Run new tests AND the existing scenario suites** (refactor must be behavior-neutral):

`SECRET_KEY=test python -m unittest tests.test_scenario_dimensions tests.test_scenario_engine -v` — all PASS.

- [ ] **Step 5: Commit**

```bash
git add app/services/scenario_dimensions.py tests/test_scenario_dimensions.py
git commit -m "refactor: extract shared fixed-logic bucket helpers"
```

---

### Task 3: `build_context_pack` + atomic write in `refresh_splits`

**Files:**
- Modify: `app/services/scenario_dimensions.py` (add `build_context_pack`)
- Modify: `app/services/scenario_engine.py` (`refresh_splits` writes the pack)
- Test: `tests/test_scenario_engine.py` (append)

**Interfaces:**
- Produces: `build_context_pack(df: pd.DataFrame, odds_df: pd.DataFrame | None) -> dict` with payload
  `{'season': str, 'total_bins': list[float] | None, 'pace_bins': list[float] | None,
    'team_game_poss': dict[str, float], 'team_def_tier': dict[str, str]}`
  (bins are pd.qcut `retbins` arrays, 4 floats for 3 buckets; None when the season slice can't support the cut — mirrors `_safe_qcut`).
- Produces: `refresh_splits` upserts the single `ScenarioContextPack` row for the sport in the SAME transaction as the splits. (The `player_crosswalk.clear_cache()` call on refresh is added in Task 4, where that module exists — this task does NOT reference it.)

- [ ] **Step 1: Write the failing tests** (append to `tests/test_scenario_engine.py`; reuse that file's existing store-seeding helpers — it has fixtures seeding `HistoricalGameLog` + `HistoricalGameOdds`):

```python
class TestContextPack(BaseTestCase):

    def test_build_context_pack_payload_shape(self):
        import pandas as pd
        from app.services.scenario_dimensions import (
            build_context_pack, load_frame, load_odds_frame,
        )
        with self.app.app_context():
            _seed_store_two_teams(games=12)     # existing helper in this file
            pack = build_context_pack(load_frame(), load_odds_frame())
        self.assertIn('season', pack)
        self.assertEqual(len(pack['total_bins']), 4)
        self.assertEqual(len(pack['pace_bins']), 4)
        self.assertTrue(all(t in ('top10', 'mid', 'bottom10')
                            for t in pack['team_def_tier'].values()))
        self.assertTrue(all(v > 0 for v in pack['team_game_poss'].values()))

    def test_refresh_splits_writes_pack_atomically(self):
        import json
        from app.models import ScenarioContextPack
        from app.services.scenario_engine import refresh_splits
        with self.app.app_context():
            _seed_store_two_teams(games=12)
            refresh_splits(force=True, min_games=1)
            row = ScenarioContextPack.query.filter_by(sport='nba').first()
            self.assertIsNotNone(row)
            payload = json.loads(row.payload)
            self.assertIn('team_def_tier', payload)
            # refresh again: still exactly one row (upsert, not append)
            refresh_splits(force=True, min_games=1)
            self.assertEqual(ScenarioContextPack.query.count(), 1)
```

(If `_seed_store_two_teams` doesn't exist under that exact name, use the file's actual store-seeding helper; it must seed ≥2 teams with scores and odds so def tier/pace/total are computable. If none seeds odds, extend the helper to also insert `HistoricalGameOdds` rows with `total`, `spread`, `favored`, `espn_game_id` matching the games.)

- [ ] **Step 2: Run, expect FAIL** (ImportError on `build_context_pack`):

`SECRET_KEY=test python -m unittest tests.test_scenario_engine.TestContextPack -v`

- [ ] **Step 3: Implement `build_context_pack`** in `app/services/scenario_dimensions.py`:

```python
def build_context_pack(df: pd.DataFrame,
                       odds_df: pd.DataFrame | None) -> dict:
    """Live-context lookup pack: quantile edges + team maps for the CURRENT
    season, computed from the same frame refresh_splits just used (so pack
    and splits can never disagree)."""
    df = df.copy()
    season = sorted(df['season'].unique())[-1]
    cur = df[df['season'] == season]

    def _qbins(s: pd.Series) -> list | None:
        s = s.dropna()
        if s.nunique() < 3:
            return None
        try:
            _, bins = pd.qcut(s, 3, retbins=True, duplicates='drop')
        except ValueError:
            return None
        return [float(b) for b in bins] if len(bins) == 4 else None

    tg = _team_games(cur)
    tg['poss'] = tg['fga'] + 0.44 * tg['fta'] + tg['tov']
    game_poss = tg.groupby('game_id', as_index=False).agg(poss=('poss', 'sum'))
    pace_bins = _qbins(game_poss['poss'])
    team_poss = (tg.merge(game_poss.rename(columns={'poss': 'game_poss'}),
                          on='game_id')
                   .groupby('team_abbr')['game_poss'].mean())

    allowed = tg.groupby('team_abbr')['opp_score'].mean().dropna()
    pct = allowed.rank(pct=True)
    def_tier = pd.cut(pct, bins=[0, 1 / 3, 2 / 3, 1.0001],
                      labels=['top10', 'mid', 'bottom10']).astype(str)

    total_bins = None
    if odds_df is not None and not odds_df.empty:
        o = odds_df.copy()
        o['game_date'] = pd.to_datetime(o['game_date'])
        o['season_key'] = o['game_date'].map(
            lambda d: f"{d.year}-{str(d.year + 1)[-2:]}" if d.month >= 10
            else f"{d.year - 1}-{str(d.year)[-2:]}")
        total_bins = _qbins(o.loc[o['season_key'] == season, 'total'])

    return {
        'season': season,
        'total_bins': total_bins,
        'pace_bins': pace_bins,
        'team_game_poss': {k: float(v) for k, v in team_poss.items()},
        'team_def_tier': {k: str(v) for k, v in def_tier.items()},
    }
```

- [ ] **Step 4: Wire into `refresh_splits`** (`app/services/scenario_engine.py`). After the splits bulk-insert completes and BEFORE the final `db.session.commit()` of the data transaction, add:

```python
        import json as _json
        from app.models import ScenarioContextPack
        pack_payload = build_context_pack(df, load_odds_frame())
        ScenarioContextPack.query.filter_by(sport=sport).delete()
        db.session.add(ScenarioContextPack(
            sport=sport, payload=_json.dumps(pack_payload),
            computed_at=computed_at))
```

(`build_context_pack` is imported from `scenario_dimensions` alongside the existing imports at line 19. Place the block inside the same try, adjacent to the splits DELETE+INSERT so one commit covers both.)

- [ ] **Step 5: Run, expect PASS**, then the full scenario suites:

`SECRET_KEY=test python -m unittest tests.test_scenario_engine tests.test_scenario_dimensions -v`

- [ ] **Step 6: Commit**

```bash
git add app/services/scenario_dimensions.py app/services/scenario_engine.py tests/test_scenario_engine.py
git commit -m "feat: persist scenario context pack atomically with splits"
```

---

### Task 4: `player_crosswalk` resolver

**Files:**
- Create: `app/services/player_crosswalk.py`
- Modify: `app/services/scenario_engine.py` (refresh clears the cache)
- Test: `tests/test_player_crosswalk.py` (create)

**Interfaces:**
- Produces:
  - `normalize_name(name: str) -> str`
  - `resolve_espn_id(player_name: str) -> str | None`
  - `clear_cache() -> None`
  - `OVERRIDES: dict[str, str]` (normalized-name → espn id; ships empty)

- [ ] **Step 1: Write the failing tests** (create `tests/test_player_crosswalk.py`):

```python
"""Tests for the live-name -> ESPN-id crosswalk resolver."""
from datetime import datetime, timezone
from tests.base import BaseTestCase          # match the import used by sibling test files
from app import db
from app.models import ScenarioSplit


def _seed_split(player_id: str, player_name: str):
    db.session.add(ScenarioSplit(
        sport='nba', player_id=player_id, player_name=player_name,
        stat='pts', dim1='home_away', bucket1='home', season_scope='all',
        n=10, raw_mean=20.0, shrunk_mean=19.5, baseline_mean=19.0,
        computed_at=datetime.now(timezone.utc)))
    db.session.commit()


class TestNormalizeName(BaseTestCase):

    def test_strips_accents_case_punctuation_and_suffixes(self):
        from app.services.player_crosswalk import normalize_name
        self.assertEqual(normalize_name('Nikola Jokić'), 'nikola jokic')
        self.assertEqual(normalize_name('Jaren Jackson Jr.'), 'jaren jackson')
        self.assertEqual(normalize_name("De'Aaron Fox"), 'deaaron fox')
        self.assertEqual(normalize_name('Trey Murphy III'), 'trey murphy')


class TestResolveEspnId(BaseTestCase):

    def test_resolves_and_caches(self):
        from app.services import player_crosswalk as xw
        with self.app.app_context():
            _seed_split('4396971', 'Nikola Jokić')
            xw.clear_cache()
            self.assertEqual(xw.resolve_espn_id('Nikola Jokic'), '4396971')

    def test_collision_drops_both_never_guesses(self):
        from app.services import player_crosswalk as xw
        with self.app.app_context():
            _seed_split('111', 'Jalen Williams')
            _seed_split('222', 'Jaylen Williams')  # normalizes differently — fine
            _seed_split('333', 'Jalen Williams')   # true collision with 111
            xw.clear_cache()
            self.assertIsNone(xw.resolve_espn_id('Jalen Williams'))
            self.assertEqual(xw.resolve_espn_id('Jaylen Williams'), '222')

    def test_unresolved_returns_none_and_override_wins(self):
        from app.services import player_crosswalk as xw
        with self.app.app_context():
            xw.clear_cache()
            self.assertIsNone(xw.resolve_espn_id('Nobody Man'))
            xw.OVERRIDES['nobody man'] = '999'
            try:
                self.assertEqual(xw.resolve_espn_id('Nobody Man'), '999')
            finally:
                xw.OVERRIDES.pop('nobody man')
```

(Adjust the `BaseTestCase` import to match sibling test files' actual import if different — copy whatever `tests/test_scenario_engine.py` uses. NOTE: the collision test seeds one split row per player; `ScenarioSplit` has a uniqueness constraint over (sport, player_id, stat, dims…) — distinct player_ids don't collide with it.)

- [ ] **Step 2: Run, expect FAIL** (ModuleNotFoundError):

`SECRET_KEY=test python -m unittest tests.test_player_crosswalk -v`

- [ ] **Step 3: Implement** (create `app/services/player_crosswalk.py`):

```python
"""Live player-name -> ESPN athlete-id resolver.

ScenarioSplit rows are keyed by ESPN ids (the historical store's namespace)
but live scoring only has display names (odds API / NBA namespace). The
bridge is the name: ~580 split players, resolved via aggressive
normalization against ScenarioSplit's own (player_id, player_name) pairs.
Collisions are dropped, never guessed — a prop either matches the right
player or shows no scenario signal.
"""

import logging
import re
import unicodedata
from functools import lru_cache

from flask import current_app

from app.models import ScenarioSplit

logger = logging.getLogger(__name__)

_SUFFIXES = {'jr', 'sr', 'ii', 'iii', 'iv', 'v'}

# Normalized-name -> ESPN id, for spellings normalization can't bridge.
OVERRIDES: dict[str, str] = {}


def normalize_name(name: str) -> str:
    ascii_name = unicodedata.normalize('NFKD', name or '').encode(
        'ascii', 'ignore').decode('ascii')
    cleaned = re.sub(r"[^a-z ]", '', ascii_name.lower().replace('-', ' '))
    tokens = [t for t in cleaned.split() if t not in _SUFFIXES]
    return ' '.join(tokens)


@lru_cache(maxsize=None)
def _name_map(_app_identity: int) -> dict:
    pairs = (ScenarioSplit.query
             .with_entities(ScenarioSplit.player_id, ScenarioSplit.player_name)
             .distinct().all())
    mapping: dict[str, str] = {}
    collided: set[str] = set()
    for pid, pname in pairs:
        key = normalize_name(pname)
        if not key:
            continue
        if key in mapping and mapping[key] != str(pid):
            collided.add(key)
            continue
        mapping[key] = str(pid)
    for key in collided:
        mapping.pop(key, None)
        logger.warning("player_crosswalk: name collision dropped: %r", key)
    return mapping


def resolve_espn_id(player_name: str) -> str | None:
    key = normalize_name(player_name)
    if not key:
        return None
    if key in OVERRIDES:
        return OVERRIDES[key]
    return _name_map(id(current_app._get_current_object())).get(key)


def clear_cache() -> None:
    _name_map.cache_clear()
```

- [ ] **Step 4: Clear the cache on refresh.** In `app/services/scenario_engine.py`, inside `refresh_splits` immediately after the successful data commit:

```python
        from app.services.player_crosswalk import clear_cache
        clear_cache()
```

- [ ] **Step 5: Run, expect PASS:**

`SECRET_KEY=test python -m unittest tests.test_player_crosswalk tests.test_scenario_engine -v`

- [ ] **Step 6: Commit**

```bash
git add app/services/player_crosswalk.py app/services/scenario_engine.py tests/test_player_crosswalk.py
git commit -m "feat: name-to-ESPN-id crosswalk resolver from ScenarioSplit"
```

---

### Task 5: Spreads in the combined odds fetch

**Files:**
- Modify: `app/services/nba_service.py` (`fetch_odds_combined` ~line 184, `get_todays_games` ~line 1094, facade `fetch_odds_combined` ~line 1281)
- Test: `tests/test_nba_service.py` (append; follow that file's existing mocked-response pattern for `fetch_odds_combined`)

**Interfaces:**
- Produces: `fetch_odds_combined() -> tuple[dict, dict, dict]` — now `(totals_map, h2h_map, spreads_map)` where `spreads_map: {matchup_key -> {'spread': float, 'favored': 'home'|'away'}}` (spread stored as a POSITIVE magnitude; `favored` says which side). Game dicts from `get_todays_games` gain `game['spread']: float | None` and `game['favored_side']: 'home'|'away'|None`.
- IMPORTANT: this widens an existing tuple return — update EVERY caller of `fetch_odds_combined` (grep: `get_todays_games`, the `NBAService` facade method, and any tests unpacking 2 values).

- [ ] **Step 1: Write the failing test** (append to `tests/test_nba_service.py`, using the same mock style as the existing `fetch_odds_combined` tests in that file — mock `ODDS_BUDGET.budgeted_get` to return a canned Odds API JSON):

```python
    def test_fetch_odds_combined_returns_spreads_map(self):
        canned = [{
            "home_team": "Denver Nuggets", "away_team": "Los Angeles Lakers",
            "bookmakers": [{
                "key": "fanduel",
                "markets": [
                    {"key": "totals", "outcomes": [
                        {"name": "Over", "point": 228.5},
                        {"name": "Under", "point": 228.5}]},
                    {"key": "h2h", "outcomes": [
                        {"name": "Denver Nuggets", "price": -320},
                        {"name": "Los Angeles Lakers", "price": 260}]},
                    {"key": "spreads", "outcomes": [
                        {"name": "Denver Nuggets", "point": -8.5},
                        {"name": "Los Angeles Lakers", "point": 8.5}]},
                ],
            }],
        }]
        with patch('app.services.nba_service.ODDS_BUDGET.budgeted_get') as get:
            get.return_value.json.return_value = canned
            get.return_value.raise_for_status.return_value = None
            from app.services.nba_service import fetch_odds_combined, _matchup_key
            totals, h2h, spreads = fetch_odds_combined()
        key = _matchup_key("Denver Nuggets", "Los Angeles Lakers")
        self.assertEqual(spreads[key], {'spread': 8.5, 'favored': 'home'})
```

(Adapt the mock target/shape to exactly match the file's existing combined-odds tests — copy their setup verbatim and add the `spreads` market.)

- [ ] **Step 2: Run, expect FAIL** (ValueError: not enough values to unpack):

`SECRET_KEY=test python -m unittest tests.test_nba_service -v 2>&1 | tail -5`

- [ ] **Step 3: Implement.** In `fetch_odds_combined`:
- request `"markets": "totals,h2h,spreads"` (same single API call — no extra request; The Odds API bills per market-region, so cost per call rises modestly; call FREQUENCY is unchanged and still governed by `ODDS_BUDGET`).
- parse the `spreads` market: the home team's outcome `point` is negative when home is favored. Build:

```python
    spreads_map: dict = {}
    # inside the per-game loop, alongside totals/h2h parsing:
    for market in bookmaker.get("markets", []):
        ...
        elif market.get("key") == "spreads":
            for outcome in market.get("outcomes", []):
                if outcome.get("name") == home_team:
                    point = outcome.get("point")
                    if point is not None:
                        spreads_map[key] = {
                            "spread": abs(float(point)),
                            "favored": "home" if float(point) < 0 else "away",
                        }
```

- return `totals_map, h2h_map, spreads_map`; update `get_todays_games`:

```python
    totals, h2h, spreads = fetch_odds_combined()
    ...
        game["over_under_line"] = totals.get(key)
        sp = spreads.get(key) or {}
        game["spread"] = sp.get("spread")
        game["favored_side"] = sp.get("favored")
```

- update the `NBAService` facade method's docstring/return passthrough (no change needed beyond the widened tuple) and fix any existing tests unpacking two values.

- [ ] **Step 4: Run the service suite, expect PASS:**

`SECRET_KEY=test python -m unittest tests.test_nba_service -v 2>&1 | tail -5`

- [ ] **Step 5: Commit**

```bash
git add app/services/nba_service.py tests/test_nba_service.py
git commit -m "feat: spreads market in combined odds fetch"
```

---

### Task 6: `live_context.build_live_context`

**Files:**
- Create: `app/services/live_context.py`
- Test: `tests/test_live_context.py` (create)

**Interfaces:**
- Consumes: `ScenarioContextPack` (Task 1/3 payload shape), shared helpers (Task 2), `HistoricalGameLog` (rest/role), `espn_mapping.normalize_abbr`.
- Produces:
  - `get_live_pack(sport='nba') -> tuple[dict | None, bool]` — `(payload, fresh)`; fresh = `computed_at` within `MAX_PACK_AGE_DAYS`.
  - `build_live_context(espn_id, *, team_abbr, opponent_abbr, is_home, game_date=None, total=None, spread=None, favored_side=None, sport='nba') -> tuple[dict, bool]` — `(context, pack_fresh)`. Emits ONLY populatable dims with stored-label values; `game_script`/`teammate_context` never present.
  - `MAX_PACK_AGE_DAYS = 7`.

- [ ] **Step 1: Write the failing tests** (create `tests/test_live_context.py`):

```python
"""Tests for the live scenario-context builder."""
import json
from datetime import date, datetime, timedelta, timezone
from tests.base import BaseTestCase          # match sibling imports
from app import db
from app.models import HistoricalGameLog, ScenarioContextPack


def _seed_pack(computed_at=None):
    db.session.add(ScenarioContextPack(
        sport='nba',
        payload=json.dumps({
            'season': '2025-26',
            'total_bins': [200.0, 221.0, 229.0, 260.0],
            'pace_bins': [180.0, 196.0, 204.0, 230.0],
            'team_game_poss': {'DEN': 198.0, 'LAL': 207.0},
            'team_def_tier': {'DEN': 'top10', 'LAL': 'bottom10'},
        }),
        computed_at=computed_at or datetime.now(timezone.utc)))
    db.session.commit()


def _seed_history(player_id='558', n=6, starter=True, end=date(2026, 1, 10)):
    for i in range(n):
        db.session.add(HistoricalGameLog(
            sport='nba', player_id=player_id, player_name='Test Player',
            team_abbr='DEN', opp_abbr='LAL', game_id=f'g{i}',
            game_date=end - timedelta(days=(n - i) * 2), season='2025-26',
            home_away='home', win_loss='W', starter=starter,
            stats={'pts': 20.0}, fetched_at=datetime.now(timezone.utc)))
    db.session.commit()


class TestBuildLiveContext(BaseTestCase):

    def _ctx(self, **kw):
        from app.services.live_context import build_live_context
        args = dict(team_abbr='DEN', opponent_abbr='LAL', is_home=True,
                    game_date=date(2026, 1, 12), total=228.5,
                    spread=8.5, favored_side='home')
        args.update(kw)
        return build_live_context('558', **args)

    def test_full_context_with_pack_and_history(self):
        with self.app.app_context():
            _seed_pack(); _seed_history()
            ctx, fresh = self._ctx()
        self.assertTrue(fresh)
        self.assertEqual(ctx['home_away'], 'home')
        self.assertEqual(ctx['season_segment'], 'mid')
        self.assertEqual(ctx['rest_bucket'], '1')       # last game 01-10, game 01-12
        self.assertEqual(ctx['role'], 'starter')
        self.assertEqual(ctx['opp_def_tier'], 'bottom10')
        self.assertEqual(ctx['pace_tier'], 'mid')       # (198+207)/2=202.5 in (196,204]
        self.assertEqual(ctx['fav_dog'], 'fav_big')
        self.assertEqual(ctx['total_bucket'], 'mid')    # 228.5 in (221,229]
        self.assertNotIn('game_script', ctx)
        self.assertNotIn('teammate_context', ctx)

    def test_missing_pack_degrades_to_fixed_dims_only(self):
        with self.app.app_context():
            _seed_history()
            ctx, fresh = self._ctx()
        self.assertFalse(fresh)
        for dim in ('opp_def_tier', 'pace_tier', 'total_bucket'):
            self.assertNotIn(dim, ctx)
        self.assertIn('home_away', ctx)
        self.assertIn('fav_dog', ctx)                   # spread came from the slate

    def test_stale_pack_still_builds_but_reports_not_fresh(self):
        with self.app.app_context():
            _seed_pack(computed_at=datetime.now(timezone.utc) - timedelta(days=30))
            _seed_history()
            ctx, fresh = self._ctx()
        self.assertFalse(fresh)
        self.assertIn('opp_def_tier', ctx)

    def test_no_history_first_game_conventions(self):
        with self.app.app_context():
            _seed_pack()
            ctx, _ = self._ctx()
        self.assertEqual(ctx['rest_bucket'], '3+')      # no prior game -> 99 -> 3+
        self.assertNotIn('role', ctx)                   # no starter evidence -> absent

    def test_no_spread_omits_fav_dog(self):
        with self.app.app_context():
            _seed_pack(); _seed_history()
            ctx, _ = self._ctx(spread=None, favored_side=None)
        self.assertNotIn('fav_dog', ctx)
```

- [ ] **Step 2: Run, expect FAIL** (ModuleNotFoundError):

`SECRET_KEY=test python -m unittest tests.test_live_context -v`

- [ ] **Step 3: Implement** (create `app/services/live_context.py`):

```python
"""Pre-game scenario context for live prop scoring.

Builds the dim->bucket dict that agreement_score matches against
ScenarioSplit buckets. Emits ONLY dimensions knowable before tip-off, with
bucket labels byte-identical to what refresh_splits stored (fixed logic is
shared via scenario_dimensions helpers; quantile-dependent buckets come from
the persisted ScenarioContextPack). game_script (realized margin) and
teammate_context (needs injury data) are never emitted.
"""

import json
import logging
from datetime import date, datetime, timedelta, timezone

from app.models import HistoricalGameLog, ScenarioContextPack
from app.services.espn_mapping import normalize_abbr
from app.services.scenario_dimensions import (
    fav_dog_label, rest_bucket_label, season_segment_label,
)
from app.utils.time_helpers import ET

logger = logging.getLogger(__name__)

MAX_PACK_AGE_DAYS = 7
ROLE_LOOKBACK = 5
ROLE_STARTER_MIN = 3


def get_live_pack(sport: str = 'nba') -> tuple[dict | None, bool]:
    row = ScenarioContextPack.query.filter_by(sport=sport).first()
    if row is None:
        return None, False
    computed = row.computed_at
    if computed.tzinfo is None:
        computed = computed.replace(tzinfo=timezone.utc)
    fresh = (datetime.now(timezone.utc) - computed) <= timedelta(
        days=MAX_PACK_AGE_DAYS)
    try:
        return json.loads(row.payload), fresh
    except ValueError:
        logger.warning("live_context: unreadable pack payload for %s", sport)
        return None, False


def _bucket_from_bins(value: float, bins: list | None,
                      labels: tuple = ('low', 'mid', 'high')) -> str | None:
    if not bins or len(bins) != 4:
        return None
    if value <= bins[1]:
        return labels[0]
    if value <= bins[2]:
        return labels[1]
    return labels[2]


def build_live_context(espn_id: str, *, team_abbr: str, opponent_abbr: str,
                       is_home: bool, game_date: date | None = None,
                       total: float | None = None,
                       spread: float | None = None,
                       favored_side: str | None = None,
                       sport: str = 'nba') -> tuple[dict, bool]:
    as_of = game_date or datetime.now(ET).date()   # ET convention, as everywhere
    ctx: dict = {'home_away': 'home' if is_home else 'away'}

    segment = season_segment_label(as_of)
    if segment is not None:
        ctx['season_segment'] = segment

    recent = (HistoricalGameLog.query
              .filter(HistoricalGameLog.sport == sport,
                      HistoricalGameLog.player_id == str(espn_id),
                      HistoricalGameLog.game_date < as_of)
              .order_by(HistoricalGameLog.game_date.desc())
              .limit(ROLE_LOOKBACK).all())
    if recent:
        days_rest = (as_of - recent[0].game_date).days - 1
        ctx['rest_bucket'] = rest_bucket_label(days_rest)
        flags = [r.starter for r in recent if r.starter is not None]
        if flags:
            started = sum(1 for f in flags if f)
            ctx['role'] = ('starter' if started >= ROLE_STARTER_MIN
                           else 'bench')
    else:
        ctx['rest_bucket'] = rest_bucket_label(99)   # first-game convention

    if spread is not None and favored_side in ('home', 'away'):
        team_is_favored = (favored_side == 'home') == is_home
        ctx['fav_dog'] = fav_dog_label(float(spread), team_is_favored)

    pack, fresh = get_live_pack(sport)
    if pack:
        opp = normalize_abbr((opponent_abbr or '').strip().upper())
        team = normalize_abbr((team_abbr or '').strip().upper())
        tier = pack.get('team_def_tier', {}).get(opp)
        if tier:
            ctx['opp_def_tier'] = tier
        poss = pack.get('team_game_poss', {})
        if team in poss and opp in poss:
            est = (poss[team] + poss[opp]) / 2.0
            pace = _bucket_from_bins(est, pack.get('pace_bins'),
                                     ('slow', 'mid', 'fast'))
            if pace:
                ctx['pace_tier'] = pace
        if total is not None:
            bucket = _bucket_from_bins(float(total), pack.get('total_bins'))
            if bucket:
                ctx['total_bucket'] = bucket
    return ctx, fresh
```

- [ ] **Step 4: Run, expect PASS:**

`SECRET_KEY=test python -m unittest tests.test_live_context -v`

- [ ] **Step 5: Commit**

```bash
git add app/services/live_context.py tests/test_live_context.py
git commit -m "feat: live scenario-context builder"
```

---

### Task 7: Parity replay test (keystone)

**Files:**
- Create: `tests/test_scenario_live_parity.py`

**Interfaces:**
- Consumes: everything from Tasks 2–6 plus the REAL `build_context`/`load_frame`/`load_odds_frame`/`build_context_pack`.

**Design notes for the implementer (why the fixture is shaped this way):**
- The sampled game must be the LAST game in the fixture: the pack's def-tier map is as-of-latest, while `build_context` ranks as-of-each-date — they coincide only at the frame's end.
- STRICT equality dims: `home_away`, `season_segment`, `rest_bucket`, `total_bucket`, `fav_dog`, `opp_def_tier`. NOT `pace_tier` (training buckets the game's REALIZED possessions; live estimates from team averages — assert only a valid label) and NOT `role` (training uses the game's actual starter flag; live predicts from last-5 — the fixture makes the player an every-game starter so both say 'starter', but the assertion documents the approximation).

- [ ] **Step 1: Write the test** (create `tests/test_scenario_live_parity.py`):

```python
"""Keystone parity test: live bucketing must equal historical bucketing.

Runs the REAL historical build_context over a seeded store, builds the pack
from the same frame, then replays build_live_context as-of the final game
and asserts every reconstructable dimension emits the SAME bucket label the
historical builder assigned to that row.
"""
import json
from datetime import date, datetime, timedelta, timezone
import pandas as pd
from tests.base import BaseTestCase          # match sibling imports
from app import db
from app.models import HistoricalGameLog, HistoricalGameOdds, ScenarioContextPack

TEAMS = ('DEN', 'LAL', 'BOS', 'NYK')


def _seed_league(n_rounds=6, start=date(2026, 1, 2)):
    """Round-robin-ish league: every team plays every round; player p-DEN
    plays for DEN every game as a starter. Totals/spreads seeded per game."""
    gid = 0
    for rnd in range(n_rounds):
        d = start + timedelta(days=rnd * 2)
        for a, b in ((TEAMS[0], TEAMS[1]), (TEAMS[2], TEAMS[3])):
            gid += 1
            game_id = f'pg{gid}'
            for team, opp, ha in ((a, b, 'home'), (b, a, 'away')):
                for slot in range(3):        # 3 players/team so poss vary
                    db.session.add(HistoricalGameLog(
                        sport='nba', player_id=f'{team}-{slot}',
                        player_name=f'{team} Player{slot}', team_abbr=team,
                        opp_abbr=opp, game_id=game_id, game_date=d,
                        season='2025-26', home_away=ha, win_loss='W',
                        starter=True,
                        stats={'pts': 20.0 + slot + rnd, 'reb': 5.0,
                               'ast': 4.0, 'stl': 1.0, 'blk': 0.5,
                               'tov': 2.0 + slot, 'fgm': 8.0,
                               'fga': 15.0 + slot + rnd, 'fg3m': 2.0,
                               'fg3a': 6.0, 'ftm': 3.0, 'fta': 4.0 + rnd,
                               'minutes': 30.0, 'plus_minus': 3.0,
                               'usage_pct': 0.2, 'team_score': 110.0 + rnd,
                               'opp_score': 104.0 + slot},
                        fetched_at=datetime.now(timezone.utc)))
            db.session.add(HistoricalGameOdds(
                game_date=d, home_abbr=a, away_abbr=b,
                spread=4.5 + rnd, favored='home', total=220.0 + rnd * 3,
                moneyline_home=-180, moneyline_away=150,
                is_playoff=False, source='test', espn_game_id=game_id))
    db.session.commit()


class TestLiveHistoricalParity(BaseTestCase):

    def test_live_buckets_equal_historical_buckets_for_final_game(self):
        from app.services.live_context import build_live_context
        from app.services.scenario_dimensions import (
            build_context, build_context_pack, load_frame, load_odds_frame,
        )
        with self.app.app_context():
            _seed_league()
            frame, odds = load_frame(), load_odds_frame()
            ctx_df = build_context(frame, odds_df=odds)

            row = (ctx_df[ctx_df['player_id'] == 'DEN-0']
                   .sort_values('game_date').iloc[-1])
            game_odds = HistoricalGameOdds.query.filter_by(
                espn_game_id=row['game_id']).first()

            db.session.add(ScenarioContextPack(
                sport='nba',
                payload=json.dumps(build_context_pack(frame, odds)),
                computed_at=datetime.now(timezone.utc)))
            db.session.commit()

            is_home = row['home_away'] == 'home'
            live, fresh = build_live_context(
                'DEN-0', team_abbr='DEN', opponent_abbr='LAL',
                is_home=is_home, game_date=row['game_date'].date(),
                total=float(game_odds.total), spread=float(game_odds.spread),
                favored_side=str(game_odds.favored))

        self.assertTrue(fresh)
        strict = {'home_away': 'ctx_home_away',
                  'season_segment': 'ctx_season_segment',
                  'rest_bucket': 'ctx_rest_bucket',
                  'total_bucket': 'ctx_total_bucket',
                  'fav_dog': 'ctx_fav_dog',
                  'opp_def_tier': 'ctx_opp_def_tier'}
        for live_dim, hist_col in strict.items():
            hist_val = row[hist_col]
            if pd.isna(hist_val):
                continue          # dim historical builder couldn't populate
            self.assertIn(live_dim, live,
                          f'live context missing {live_dim}')
            self.assertEqual(live[live_dim], str(hist_val),
                             f'parity broken for {live_dim}')
        if 'pace_tier' in live:
            self.assertIn(live['pace_tier'], ('slow', 'mid', 'fast'))
        self.assertEqual(live.get('role'), 'starter')   # every-game starter fixture
        self.assertNotIn('game_script', live)
```

- [ ] **Step 2: Run — this test must PASS against Tasks 2–6 as implemented.** If any strict dim fails, the LIVE side is wrong (historical is the reference): fix `live_context`/`build_context_pack`, not the test.

`SECRET_KEY=test python -m unittest tests.test_scenario_live_parity -v`

- [ ] **Step 3: Commit**

```bash
git add tests/test_scenario_live_parity.py
git commit -m "test: live-vs-historical bucket parity keystone"
```

---

### Task 8: `ValueDetector` integration (flag-gated signal + nudge)

**Files:**
- Modify: `app/services/value_detector.py` (`score_prop` signature + result dict; new `_scenario_signal` + `_use_scenario_signal` methods near `_use_distributional_model` ~line 341; `score_all_todays_props` call site ~line 607)
- Test: `tests/test_services.py` (append to `TestValueDetector`)

**Interfaces:**
- Consumes: `resolve_espn_id`, `build_live_context`, `agreement_score` (existing: `(player_id, stat, line, context) -> (score, n_matches)`).
- Produces: `score_prop(..., spread=None, favored_side=None)` result gains
  `scenario_agreement: float | None`, `scenario_matches: int | None`; context note
  `"Scenario splits: {n} matches, lean {over|under} {score:+.2f}"`; bounded tier nudge.
- Constants (in `value_detector.py`): `SCENARIO_MIN_MATCHES = 5`, `SCENARIO_STRONG_THRESHOLD = 0.5`,
  `PROP_TO_SPLIT_STAT = {'player_points': 'pts', 'player_rebounds': 'reb', 'player_assists': 'ast', 'player_threes': 'fg3m', 'player_points_rebounds_assists': 'pra'}`,
  `_TIER_DEMOTE = {'strong': 'moderate', 'moderate': 'slight', 'slight': 'no_edge'}`.
- Nudge order: applied AFTER the Model 2 win-probability adjustment, immediately before the result dict is assembled — scenario has the last word, and only ever one step.

- [ ] **Step 1: Write the failing tests** (append inside `TestValueDetector` in `tests/test_services.py`; reuse the file's existing 20-log seeding pattern from `test_dist_scored_prop_displays_dist_median_as_projection`):

```python
    def _seed_scenario_player(self, pid='920', name='Scenario Player'):
        for i in range(20):
            db.session.add(PlayerGameLog(
                player_id=pid, player_name=name, team_abbr='TST',
                game_date=date(2026, 1, 1) + timedelta(days=i),
                pts=25, reb=6, ast=4, fg3m=2, minutes=33,
                stl=1, blk=0, tov=2, fgm=9, fga=18, ftm=5, fta=6, fg3a=6,
            ))
        db.session.commit()

    def test_scenario_signal_fields_note_and_demotion(self):
        from app.services.value_detector import ValueDetector
        with self.app.app_context():
            self._seed_scenario_player()
            detector = ValueDetector()
            with patch('app.services.projection_engine.find_player_id', return_value='920'), \
                 patch.dict('os.environ', {'USE_SCENARIO_SIGNAL': 'true'}), \
                 patch('app.services.player_crosswalk.resolve_espn_id', return_value='4396'), \
                 patch('app.services.live_context.build_live_context',
                       return_value=({'home_away': 'home'}, True)), \
                 patch('app.services.scenario_engine.agreement_score',
                       return_value=(-0.8, 9)):
                result = detector.score_prop(
                    'Scenario Player', 'player_points',
                    line=20.5, over_odds=-110, under_odds=-110,
                )
        self.assertEqual(result['scenario_agreement'], -0.8)
        self.assertEqual(result['scenario_matches'], 9)
        self.assertTrue(any('Scenario splits' in n for n in result['context_notes']))
        # strong disagreement demotes one tier from whatever it was
        self.assertIn(result['confidence_tier'],
                      ('moderate', 'slight', 'no_edge'))

    def test_scenario_promotion_only_from_slight(self):
        from app.services import value_detector as vd
        self.assertEqual(vd._TIER_DEMOTE['moderate'], 'slight')
        # promotion rule is a pure function — test it directly
        self.assertEqual(vd._apply_scenario_nudge('slight', 0.7, 6), 'moderate')
        self.assertEqual(vd._apply_scenario_nudge('moderate', 0.7, 6), 'moderate')
        self.assertEqual(vd._apply_scenario_nudge('moderate', -0.7, 6), 'slight')
        self.assertEqual(vd._apply_scenario_nudge('moderate', 0.7, 3), 'moderate')   # < MIN_MATCHES
        self.assertEqual(vd._apply_scenario_nudge('moderate', 0.3, 9), 'moderate')   # < threshold

    def test_flag_off_result_is_byte_identical_and_fields_none(self):
        from app.services.value_detector import ValueDetector
        with self.app.app_context():
            self._seed_scenario_player(pid='921', name='Flagoff Player')
            detector = ValueDetector()
            with patch('app.services.projection_engine.find_player_id', return_value='921'):
                result = detector.score_prop(
                    'Flagoff Player', 'player_points',
                    line=20.5, over_odds=-110, under_odds=-110,
                )
        self.assertIsNone(result['scenario_agreement'])
        self.assertIsNone(result['scenario_matches'])
        self.assertFalse(any('Scenario splits' in n for n in result['context_notes']))

    def test_scenario_exception_never_breaks_scoring(self):
        from app.services.value_detector import ValueDetector
        with self.app.app_context():
            self._seed_scenario_player(pid='922', name='Boom Player')
            detector = ValueDetector()
            with patch('app.services.projection_engine.find_player_id', return_value='922'), \
                 patch.dict('os.environ', {'USE_SCENARIO_SIGNAL': 'true'}), \
                 patch('app.services.player_crosswalk.resolve_espn_id',
                       side_effect=RuntimeError('boom')):
                result = detector.score_prop(
                    'Boom Player', 'player_points',
                    line=20.5, over_odds=-110, under_odds=-110,
                )
        self.assertIsNone(result['scenario_agreement'])
        self.assertGreater(result['model_prob_over'], 0)
```

- [ ] **Step 2: Run, expect FAIL** (KeyError `scenario_agreement` / missing `_apply_scenario_nudge`):

`SECRET_KEY=test python -m unittest tests.test_services.TestValueDetector -v 2>&1 | tail -8`

- [ ] **Step 3: Implement.** In `app/services/value_detector.py`:

Module level (near the tier constants):

```python
SCENARIO_MIN_MATCHES = 5
SCENARIO_STRONG_THRESHOLD = 0.5
PROP_TO_SPLIT_STAT = {
    'player_points': 'pts',
    'player_rebounds': 'reb',
    'player_assists': 'ast',
    'player_threes': 'fg3m',
    'player_points_rebounds_assists': 'pra',
}
_TIER_DEMOTE = {'strong': 'moderate', 'moderate': 'slight', 'slight': 'no_edge'}


def _apply_scenario_nudge(tier: str, agreement: float, matches: int) -> str:
    """One bounded step: strong disagreement demotes; strong agreement can
    only promote slight -> moderate (a scenario signal never manufactures
    'strong')."""
    if matches < SCENARIO_MIN_MATCHES:
        return tier
    if agreement <= -SCENARIO_STRONG_THRESHOLD:
        return _TIER_DEMOTE.get(tier, tier)
    if agreement >= SCENARIO_STRONG_THRESHOLD and tier == 'slight':
        return 'moderate'
    return tier
```

Methods on `ValueDetector` (next to `_use_distributional_model`):

```python
    def _use_scenario_signal(self) -> bool:
        return os.getenv('USE_SCENARIO_SIGNAL', 'false').lower() == 'true'

    def _scenario_signal(self, player_name, prop_type, line, opponent_name,
                         team_name, is_home, game_date, game_total_line,
                         spread, favored_side):
        """(agreement, matches, pack_fresh) or None. Never raises."""
        stat = PROP_TO_SPLIT_STAT.get(prop_type)
        if stat is None:
            return None
        from app.services.player_crosswalk import resolve_espn_id
        espn_id = resolve_espn_id(player_name)
        if espn_id is None:
            return None
        from app.services.live_context import build_live_context
        context, fresh = build_live_context(
            espn_id, team_abbr=team_name, opponent_abbr=opponent_name,
            is_home=is_home, game_date=game_date,
            total=game_total_line or None, spread=spread,
            favored_side=favored_side)
        from app.services.scenario_engine import agreement_score
        score, matches = agreement_score(espn_id, stat, line, context)
        if matches == 0:
            return None
        return score, matches, fresh
```

In `score_prop`: add `spread: Optional[float] = None, favored_side: Optional[str] = None` parameters (after `game_total_line`). After the Model 2 block and its `context_notes = _sanitize_context_notes(context_notes)` line, add:

```python
        scenario_agreement = None
        scenario_matches = None
        if self._use_scenario_signal():
            try:
                signal = self._scenario_signal(
                    player_name, prop_type, line, opponent_name, team_name,
                    is_home, game_date, game_total_line, spread, favored_side)
            except Exception as exc:
                logger.warning(
                    "Scenario signal failed for %s/%s: %s",
                    player_name, prop_type, exc)
                signal = None
            if signal is not None:
                scenario_agreement, scenario_matches, pack_fresh = signal
                lean = 'over' if scenario_agreement >= 0 else 'under'
                context_notes.append(
                    f"Scenario splits: {scenario_matches} matches, "
                    f"lean {lean} {scenario_agreement:+.2f}")
                if pack_fresh:      # stale conditioning never nudges tiers
                    confidence_tier = _apply_scenario_nudge(
                        confidence_tier, scenario_agreement, scenario_matches)
```

In the result dict add:

```python
            'scenario_agreement': scenario_agreement,
            'scenario_matches': scenario_matches,
```

In `score_all_todays_props`, thread the new fields at the `score_prop` call (~line 607):

```python
                        spread=game.get('spread'),
                        favored_side=game.get('favored_side'),
```

- [ ] **Step 4: Run the detector suite + full flag-off regression, expect PASS:**

`SECRET_KEY=test python -m unittest tests.test_services.TestValueDetector -v 2>&1 | tail -5`

- [ ] **Step 5: Commit**

```bash
git add app/services/value_detector.py tests/test_services.py
git commit -m "feat: scenario agreement signal + bounded tier nudge in ValueDetector"
```

---

### Task 9: Full gates + docs touch

**Files:**
- Modify: `CLAUDE.md` (scheduler jobs line only if job count changed — it did NOT; skip if untouched)
- Modify: `docs/superpowers/specs/2026-07-17-plan-c-increment-2-scenario-signal-design.md` — record any implementation deviations in a short "## Deviations" section (empty section is fine).

- [ ] **Step 1: Full suite FOREGROUND + coverage:**

```bash
SECRET_KEY=test python -m coverage run -m unittest discover -s tests
python -m coverage report --include="app/*"
```
Expected: all tests pass, TOTAL ≥ 80%.

- [ ] **Step 2: Lint + security:**

```bash
ruff check .
bandit -q -r app -x tests -ll
```
Expected: both clean.

- [ ] **Step 3: Commit any deviation notes:**

```bash
git add docs/superpowers/specs/2026-07-17-plan-c-increment-2-scenario-signal-design.md
git commit -m "docs: record increment 2 implementation deviations"
```

- [ ] **Step 4: Report** — commits, test/coverage/lint output, how the parity test's strict-dim set fared, and anything not implemented (with reason) rather than silently substituted.

---

## Post-merge runbook (NOT part of this plan's execution — operator steps)
1) Back up `instance/app.db`; apply migration via `flask_migrate.upgrade()` from Python.
2) `flask refresh-splits --force` → writes the first ScenarioContextPack.
3) Replay spot-check vs direct SQL for 2-3 known players.
4) Flip `USE_SCENARIO_SIGNAL=true` when satisfied.
