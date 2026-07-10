# Plan B: Scenario Engine Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Conditional performance splits per (player, stat) over HistoricalGameLog across 10 context dimensions, empirically-Bayes shrunk, materialized nightly into a ScenarioSplit table, with an agreement-score read API for Plan C.

**Architecture:** Two data-enrichment prerequisites (team scores in the stats payload; a HistoricalGameOdds table imported from the validated Kaggle lines CSV), then a registry of vectorized dimension functions (`scenario_dimensions.py`), a pandas engine (`scenario_engine.py`) that computes singles + pairwise splits with shrinkage and bulk-writes ScenarioSplit, and scheduler/CLI wiring. Spec: `docs/superpowers/specs/2026-07-10-plan-b-scenario-engine-design.md`.

**Tech Stack:** Flask 3.1, SQLAlchemy + Alembic (flask-migrate), pandas 2.2.3, APScheduler CronTrigger, unittest + coverage, ruff + bandit.

## Global Constraints

- All date logic ET (`from app.utils.time_helpers import ET`); ESPN id namespace throughout (string player_id/game_id; abbrs NBA-normalized via `app.services.espn_mapping.normalize_abbr`).
- Shrinkage: `shrunk = (n*raw + k*baseline) / (n + k)`; baseline = player's overall mean for the stat in scope; per-stat `k` fit from league variance, clamped to [2, 25]; store splits only when `n >= 3`; players gated at `>= 15` games in the trailing 2 seasons.
- Split stats for NBA: `('pts', 'reb', 'ast', 'fg3m', 'pra')` where `pra = pts + reb + ast` (computed column, not stored in payloads).
- Singles + pairwise combos only (C(10,2)=45 pairs), never 3-way.
- `--update-stats` merges MISSING payload keys only — never overwrites a present value.
- ScenarioSplit is derived data: refresh = DELETE all rows for the sport + bulk INSERT, one transaction.
- Migration naming: run `flask --app run.py db revision -d migrations` to get the next revision id — do NOT hardcode one. The Task 2 migration also DROPS index `ix_historical_game_log_sport`.
- Test runner: `SECRET_KEY=test python -m unittest tests.<module> -v` (unittest, NOT pytest). Tests never hit network or the real instance/app.db. Full suite + `ruff check .` + `bandit -q -r app -x tests -ll` before every commit; ALL test runs in the foreground.
- Scheduler jobs registered in `init_scheduler(app)` with `_log_job` wrappers + `replace_existing=True`; CLIs registered via `register_*` functions called in `app/cli/__init__.py:register_cli`.
- Commits: conventional style, NEVER include Co-Authored-By.

## File Structure

- Modify `app/cli/hoopr_import.py` — score keys in payload; `--update-stats` mode.
- Modify `app/services/espn_history_append.py` — score keys in payload.
- Modify `app/models.py` — `ScenarioSplit`, `HistoricalGameOdds` models.
- Create `migrations/versions/<rev>_add_scenario_split_and_game_odds.py` — via autogenerate + hand-check.
- Create `app/cli/odds_import.py` — `import-betting-lines` CLI.
- Create `app/services/scenario_dimensions.py` — context builder + dimension registry.
- Create `app/services/scenario_engine.py` — k-fit, split computation, refresh, agreement score.
- Create `app/cli/scenario_commands.py` — `refresh-splits`, `show-splits` CLIs.
- Modify `app/services/scheduler.py` — job #21 `refresh_scenario_splits`.
- Tests: `tests/test_hoopr_import.py` (additions), `tests/test_espn_history_append.py` (additions), `tests/test_odds_import.py`, `tests/test_scenario_dimensions.py`, `tests/test_scenario_engine.py` (new).

---

### Task 1: Team scores in both import payloads + --update-stats

**Files:**
- Modify: `app/cli/hoopr_import.py`
- Modify: `app/services/espn_history_append.py`
- Test: `tests/test_hoopr_import.py`, `tests/test_espn_history_append.py` (append classes)

**Interfaces:**
- Consumes: existing `_rows_from_player_box(df, season, season_type_code, max_games=None) -> tuple[list[dict], dict]`; `import_hoopr_seasons(sport, seasons, season_type, from_dir, max_games) -> dict`; `append_final_game(game: dict) -> int`.
- Produces: stats payloads additionally carry `team_score: float` and `opp_score: float` on every new row from BOTH paths. `import_hoopr_seasons` gains kwarg `update_stats: bool = False` (result dict gains key `updated: int`); CLI flag `--update-stats`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_hoopr_import.py` (the module-level `_player_box_df()` fixture already has `team_score`-relevant columns only if present — first ADD to every played row of the fixture: `'team_score': 120, 'opponent_team_score': 110` for LAL rows, reversed for BOS rows, and matching values on the DNP/All-Star/playoff rows so the frame stays rectangular):

```python
class TestScoreEnrichment(BaseTestCase):

    def test_rows_carry_team_and_opp_scores(self):
        from app.cli.hoopr_import import _rows_from_player_box
        rows, _ = _rows_from_player_box(
            _player_box_df(), season='2025-26', season_type_code=2)
        lebron = next(r for r in rows if r['player_id'] == '1966')
        self.assertEqual(lebron['stats']['team_score'], 120.0)
        self.assertEqual(lebron['stats']['opp_score'], 110.0)
        tatum = next(r for r in rows if r['player_id'] == '4065648')
        self.assertEqual(tatum['stats']['team_score'], 110.0)
        self.assertEqual(tatum['stats']['opp_score'], 120.0)

    @patch('app.cli.hoopr_import._load_player_box_df')
    def test_update_stats_merges_missing_keys_only(self, mock_load):
        from app import db
        from app.cli.hoopr_import import import_hoopr_seasons
        from app.models import HistoricalGameLog
        mock_load.return_value = _player_box_df()
        with self.app.app_context():
            # first import: rows land WITH scores
            import_hoopr_seasons(seasons=1)
            row = HistoricalGameLog.query.filter_by(player_id='1966').one()
            # simulate a pre-Plan-B row: strip scores, poison pts
            st = dict(row.stats)
            del st['team_score'], st['opp_score']
            st['pts'] = 99.0                       # must NOT be overwritten
            row.stats = st
            db.session.commit()
            result = import_hoopr_seasons(seasons=1, update_stats=True)
            self.assertEqual(result['updated'], 1)  # only the stripped row
            row = HistoricalGameLog.query.filter_by(player_id='1966').one()
            self.assertEqual(row.stats['team_score'], 120.0)   # merged in
            self.assertEqual(row.stats['pts'], 99.0)           # untouched

    @patch('app.cli.hoopr_import._load_player_box_df')
    def test_update_stats_cli_flag(self, mock_load):
        mock_load.return_value = _player_box_df()
        runner = self.app.test_cli_runner()
        runner.invoke(args=['import-hoopr-logs', '--seasons', '1'])
        result = runner.invoke(args=['import-hoopr-logs', '--seasons', '1',
                                     '--update-stats'])
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIn('updated=', result.output)
```

Append to `tests/test_espn_history_append.py` (the `_summary_json()`/`_scoreboard_game()` fixtures already exist):

```python
class TestScoreEnrichmentAppend(BaseTestCase):

    @patch('app.services.espn_history_append._fetch_summary')
    def test_appended_rows_carry_scores(self, mock_fetch):
        from app.models import HistoricalGameLog
        from app.services.espn_history_append import append_final_game
        mock_fetch.return_value = _summary_json()
        with self.app.app_context():
            append_final_game(_scoreboard_game())    # home LAL 120, away GS 110
            lebron = HistoricalGameLog.query.filter_by(player_id='1966').one()
            self.assertEqual(lebron.stats['team_score'], 120.0)
            self.assertEqual(lebron.stats['opp_score'], 110.0)
            curry = HistoricalGameLog.query.filter_by(player_id='3975').one()
            self.assertEqual(curry.stats['team_score'], 110.0)
            self.assertEqual(curry.stats['opp_score'], 120.0)
```

- [ ] **Step 2: Run to verify failures**

Run: `source .venv/bin/activate && SECRET_KEY=test python -m unittest tests.test_hoopr_import.TestScoreEnrichment tests.test_espn_history_append.TestScoreEnrichmentAppend -v`
Expected: FAIL (KeyError 'team_score'; TypeError unexpected kwarg 'update_stats')

- [ ] **Step 3: Implement — hoopr_import.py**

In `_rows_from_player_box`, inside the per-record loop after `stats['plus_minus'] = ...`, add:

```python
        stats['team_score'] = _safe_float(rec.get('team_score'))
        stats['opp_score'] = _safe_float(rec.get('opponent_team_score'))
```

In `import_hoopr_seasons`, add kwarg `update_stats: bool = False`, initialize `updated = 0`, and in the per-season loop change the existing-row skip branch: when `(kwargs['player_id'], kwargs['game_id']) in existing`, if `update_stats` is true, load those rows once per season (build `existing_rows = {(r.player_id, r.game_id): r for r in HistoricalGameLog.query.filter_by(sport=sport, season=season)}` right after the `existing` set when the flag is on) and merge:

```python
                    if update_stats:
                        row = existing_rows[(kwargs['player_id'],
                                             kwargs['game_id'])]
                        merged = dict(row.stats or {})
                        missing = {k: v for k, v in kwargs['stats'].items()
                                   if k not in merged}
                        if missing:
                            merged.update(missing)
                            row.stats = merged   # reassign — JSON no mutation tracking
                            updated += 1
                    skipped += 1
                    continue
```

Include `updated` in the returned dict and in the JobLog message (`f"inserted={inserted} skipped={skipped} updated={updated}"`). Add the CLI option `@click.option('--update-stats', is_flag=True, default=False)` passing through, and include `updated=` in the CLI's `Done:` echo.

- [ ] **Step 4: Implement — espn_history_append.py**

In `append_final_game`, `home_score`/`away_score` are already computed. In the per-record row build, after `stats['usage_pct'] = ...`:

```python
        stats['team_score'] = float(home_score if is_home else away_score)
        stats['opp_score'] = float(away_score if is_home else home_score)
```

- [ ] **Step 5: Run new tests, then both full module suites**

Run: `SECRET_KEY=test python -m unittest tests.test_hoopr_import tests.test_espn_history_append -v`
Expected: all PASS (existing + new). Note: the existing `test_maps_columns_and_skips_dnp_and_other_season_types` asserts exact stats keys only via specific lookups, and `test_appends_rows_with_mapped_fields`'s key-set assertion in test_espn_history_append DOES enumerate keys — extend that `assertEqual(set(st), {...})` set with `'team_score', 'opp_score'` as part of this task.

- [ ] **Step 6: Lint + commit**

```bash
ruff check . && bandit -q -r app -x tests -ll
git add app/cli/hoopr_import.py app/services/espn_history_append.py \
        tests/test_hoopr_import.py tests/test_espn_history_append.py
git commit -m "feat: team/opp scores in history payloads + --update-stats merge mode"
```

---

### Task 2: ScenarioSplit + HistoricalGameOdds models and migration

**Files:**
- Modify: `app/models.py` (append after HistoricalGameLog)
- Create: `migrations/versions/<rev>_add_scenario_split_and_game_odds.py`
- Test: `tests/test_scenario_models.py` (new)

**Interfaces:**
- Consumes: nothing new.
- Produces (Tasks 3-6 rely on these exact fields): models below, verbatim.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_scenario_models.py
"""Model-shape tests for ScenarioSplit and HistoricalGameOdds."""

from datetime import date

from tests.helpers import BaseTestCase


class TestScenarioModels(BaseTestCase):

    def test_scenario_split_roundtrip_and_unique(self):
        from app import db
        from app.models import ScenarioSplit
        from sqlalchemy.exc import IntegrityError
        with self.app.app_context():
            kw = dict(sport='nba', player_id='1966',
                      player_name='LeBron James', stat='pts',
                      dim1='home_away', bucket1='HOME', dim2=None,
                      bucket2=None, season_scope='all', n=41,
                      raw_mean=27.1, shrunk_mean=26.8, baseline_mean=26.2)
            db.session.add(ScenarioSplit(**kw))
            db.session.commit()
            row = ScenarioSplit.query.one()
            self.assertEqual(row.bucket1, 'HOME')
            self.assertIsNotNone(row.computed_at)
            db.session.add(ScenarioSplit(**kw))
            with self.assertRaises(IntegrityError):
                db.session.commit()
            db.session.rollback()

    def test_game_odds_roundtrip_and_unique(self):
        from app import db
        from app.models import HistoricalGameOdds
        from sqlalchemy.exc import IntegrityError
        with self.app.app_context():
            kw = dict(game_date=date(2026, 11, 5), home_abbr='LAL',
                      away_abbr='GSW', spread=6.5, favored='home',
                      total=224.5, is_playoff=False)
            db.session.add(HistoricalGameOdds(**kw))
            db.session.commit()
            row = HistoricalGameOdds.query.one()
            self.assertEqual(row.source, 'kaggle')       # default
            self.assertIsNone(row.espn_game_id)
            db.session.add(HistoricalGameOdds(**kw))
            with self.assertRaises(IntegrityError):
                db.session.commit()
            db.session.rollback()
```

- [ ] **Step 2: Run to verify failure**

Run: `SECRET_KEY=test python -m unittest tests.test_scenario_models -v`
Expected: ImportError (no ScenarioSplit).

- [ ] **Step 3: Add models to `app/models.py`** (after HistoricalGameLog, matching its style)

```python
class ScenarioSplit(db.Model):
    """Materialized conditional split (single or pairwise) per player/stat.

    Derived data: rebuilt wholesale by the scenario engine — never edited.
    """

    id = db.Column(db.Integer, primary_key=True)
    sport = db.Column(db.String(10), nullable=False, default='nba')
    player_id = db.Column(db.String(20), nullable=False)
    player_name = db.Column(db.String(120), nullable=False)
    stat = db.Column(db.String(20), nullable=False)
    dim1 = db.Column(db.String(30), nullable=False)
    bucket1 = db.Column(db.String(20), nullable=False)
    dim2 = db.Column(db.String(30), nullable=True)
    bucket2 = db.Column(db.String(20), nullable=True)
    season_scope = db.Column(db.String(10), nullable=False, default='all')
    n = db.Column(db.Integer, nullable=False)
    raw_mean = db.Column(db.Float, nullable=False)
    shrunk_mean = db.Column(db.Float, nullable=False)
    baseline_mean = db.Column(db.Float, nullable=False)
    computed_at = db.Column(
        db.DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))

    __table_args__ = (
        UniqueConstraint('sport', 'player_id', 'stat', 'dim1', 'bucket1',
                         'dim2', 'bucket2', 'season_scope',
                         name='uq_scenario_split_key'),
        Index('ix_scenario_split_lookup', 'sport', 'player_id', 'stat'),
    )

    def __repr__(self) -> str:
        return (f"<ScenarioSplit {self.player_name} {self.stat} "
                f"{self.dim1}={self.bucket1}>")


class HistoricalGameOdds(db.Model):
    """Closing line context per historical game (source: Kaggle backfill)."""

    id = db.Column(db.Integer, primary_key=True)
    game_date = db.Column(db.Date, nullable=False, index=True)
    home_abbr = db.Column(db.String(10), nullable=False)
    away_abbr = db.Column(db.String(10), nullable=False)
    spread = db.Column(db.Float, nullable=False)
    favored = db.Column(db.String(4), nullable=False)   # 'home' | 'away'
    total = db.Column(db.Float, nullable=False)
    moneyline_home = db.Column(db.Float, nullable=True)
    moneyline_away = db.Column(db.Float, nullable=True)
    is_playoff = db.Column(db.Boolean, nullable=False, default=False)
    source = db.Column(db.String(20), nullable=False, default='kaggle')
    espn_game_id = db.Column(db.String(30), nullable=True)

    __table_args__ = (
        UniqueConstraint('game_date', 'home_abbr',
                         name='uq_game_odds_date_home'),
    )

    def __repr__(self) -> str:
        return f"<HistoricalGameOdds {self.game_date} {self.away_abbr}@{self.home_abbr}>"
```

- [ ] **Step 4: Run test to verify pass** (`db.create_all` in BaseTestCase picks the models up)

Run: `SECRET_KEY=test python -m unittest tests.test_scenario_models -v`
Expected: 2 PASS

- [ ] **Step 5: Generate + hand-check migration, validate on scratch SQLite**

```bash
SECRET_KEY=test DATABASE_URL=sqlite:////tmp/planb_scratch.db flask --app run.py db upgrade -d migrations   # bring scratch to current head first (fresh DB: use create_all + stamp instead if chain won't replay: python -c create_all then flask db stamp heads)
SECRET_KEY=test DATABASE_URL=sqlite:////tmp/planb_scratch.db flask --app run.py db revision --autogenerate -m "add scenario split and game odds" -d migrations
```

KNOWN GOTCHA (from Plan A ledger): the migration chain cannot replay from scratch on SQLite (b6f9fdecc99a drops a Postgres-named FK) — for the scratch DB use `create_all` + `flask db stamp heads`, THEN autogenerate. Hand-edit the generated file: keep only ScenarioSplit + HistoricalGameOdds creates, and ADD `op.drop_index('ix_historical_game_log_sport', table_name='historical_game_log')` to `upgrade()` (and the matching `create_index` in `downgrade()`). Remove any unrelated autogen noise. Validate: `db upgrade` then `db downgrade -1` then `db upgrade` on the scratch DB, all clean.

- [ ] **Step 6: Lint + commit**

```bash
ruff check . && bandit -q -r app -x tests -ll
git add app/models.py migrations/versions/ tests/test_scenario_models.py
git commit -m "feat: ScenarioSplit + HistoricalGameOdds models and migration (drops redundant sport index)"
```

---

### Task 3: import-betting-lines CLI

**Files:**
- Create: `app/cli/odds_import.py`
- Modify: `app/cli/__init__.py` (register)
- Test: `tests/test_odds_import.py`

**Interfaces:**
- Consumes: `HistoricalGameOdds`, `HistoricalGameLog`, `JobLog` models; `normalize_abbr` from `app.services.espn_mapping`; pandas.
- Produces: CLI `import-betting-lines --file PATH [--seasons-from 2024]`; callable `import_betting_lines(file: str, seasons_from: int = 2024) -> dict` returning `{'inserted': int, 'skipped': int, 'matched': int, 'unmatched': int, 'score_mismatches': int, 'errors': list[str]}`.

Kaggle CSV columns (validated 2026-07-10): `season,date,regular,playoffs,away,home,score_away,score_home,q1..ot,whos_favored,spread,total,moneyline_away,moneyline_home,h2_spread,h2_total,id_spread,id_total`. Abbrs lowercase ESPN aliases (`gs,ny,sa,no,utah,wsh,...`). `season` = end year (2026 = 2025-26).

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_odds_import.py
"""Tests for the Kaggle betting-lines importer."""

import tempfile
from datetime import date
from pathlib import Path

import pandas as pd

from tests.helpers import BaseTestCase


def _csv(rows) -> str:
    cols = ['season', 'date', 'regular', 'playoffs', 'away', 'home',
            'score_away', 'score_home', 'whos_favored', 'spread', 'total',
            'moneyline_away', 'moneyline_home']
    path = Path(tempfile.mkdtemp()) / 'lines.csv'
    pd.DataFrame(rows, columns=cols).to_csv(path, index=False)
    return str(path)


def _row(**kw):
    base = dict(season=2026, date='2025-10-21', regular=True, playoffs=False,
                away='gs', home='lal', score_away=110, score_home=120,
                whos_favored='home', spread=6.5, total=224.5,
                moneyline_away=200.0, moneyline_home=-240.0)
    base.update(kw)
    return base


class TestImportBettingLines(BaseTestCase):

    def test_imports_normalizes_and_flags(self):
        from app.cli.odds_import import import_betting_lines
        from app.models import HistoricalGameOdds
        path = _csv([_row(),
                     _row(date='2026-04-20', regular=False, playoffs=True,
                          away='ny', home='sa', whos_favored='away',
                          spread=2.5, total=215.0)])
        with self.app.app_context():
            result = import_betting_lines(path)
            self.assertEqual(result['inserted'], 2)
            reg = HistoricalGameOdds.query.filter_by(
                game_date=date(2025, 10, 21)).one()
            self.assertEqual(reg.home_abbr, 'LAL')
            self.assertEqual(reg.away_abbr, 'GSW')     # gs normalized
            self.assertEqual(reg.favored, 'home')
            self.assertFalse(reg.is_playoff)
            po = HistoricalGameOdds.query.filter_by(
                game_date=date(2026, 4, 20)).one()
            self.assertEqual(po.home_abbr, 'SAS')
            self.assertTrue(po.is_playoff)

    def test_idempotent_and_seasons_from_filter(self):
        from app.cli.odds_import import import_betting_lines
        from app.models import HistoricalGameOdds
        path = _csv([_row(), _row(season=2010, date='2009-11-01',
                                  away='bos', home='mia')])
        with self.app.app_context():
            r1 = import_betting_lines(path, seasons_from=2024)
            self.assertEqual(r1['inserted'], 1)        # 2010 filtered out
            r2 = import_betting_lines(path, seasons_from=2024)
            self.assertEqual(r2['inserted'], 0)
            self.assertEqual(r2['skipped'], 1)
            self.assertEqual(HistoricalGameOdds.query.count(), 1)

    def test_espn_game_match_and_score_crosscheck(self):
        from app import db
        from app.cli.odds_import import import_betting_lines
        from app.models import HistoricalGameOdds, HistoricalGameLog
        with self.app.app_context():
            db.session.add(HistoricalGameLog(
                sport='nba', player_id='1966', player_name='LeBron James',
                team_abbr='LAL', opp_abbr='GSW', game_id='401800123',
                game_date=date(2025, 10, 21), season='2025-26',
                home_away='HOME', win_loss='W', starter=True,
                stats={'pts': 28.0, 'team_score': 120.0,
                       'opp_score': 110.0}))
            db.session.commit()
            result = import_betting_lines(_csv([_row()]))
            self.assertEqual(result['matched'], 1)
            self.assertEqual(result['score_mismatches'], 0)
            odds = HistoricalGameOdds.query.one()
            self.assertEqual(odds.espn_game_id, '401800123')

    def test_score_mismatch_reported_not_fatal(self):
        from app import db
        from app.cli.odds_import import import_betting_lines
        from app.models import HistoricalGameLog
        with self.app.app_context():
            db.session.add(HistoricalGameLog(
                sport='nba', player_id='1966', player_name='LeBron James',
                team_abbr='LAL', opp_abbr='GSW', game_id='401800123',
                game_date=date(2025, 10, 21), season='2025-26',
                home_away='HOME', win_loss='W', starter=True,
                stats={'pts': 28.0, 'team_score': 999.0,
                       'opp_score': 110.0}))
            db.session.commit()
            result = import_betting_lines(_csv([_row()]))
            self.assertEqual(result['inserted'], 1)     # still imported
            self.assertEqual(result['score_mismatches'], 1)

    def test_cli_registered_and_reports(self):
        runner = self.app.test_cli_runner()
        result = runner.invoke(args=[
            'import-betting-lines', '--file', _csv([_row()])])
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIn('inserted=1', result.output)
        self.assertIn('matched=', result.output)
```

- [ ] **Step 2: Run to verify failure**

Run: `SECRET_KEY=test python -m unittest tests.test_odds_import -v`
Expected: ModuleNotFoundError app.cli.odds_import.

- [ ] **Step 3: Implement `app/cli/odds_import.py`**

```python
"""Import historical betting lines (Kaggle CSV) into HistoricalGameOdds.

Source dataset validated 2026-07-10: 100% join rate vs HistoricalGameLog
on (date, home team) for the overlapping seasons; abbrs are ESPN aliases.
"""

import logging
from datetime import datetime, timezone

import click

from app import db
from app.models import HistoricalGameLog, HistoricalGameOdds, JobLog
from app.services.espn_mapping import normalize_abbr

logger = logging.getLogger(__name__)


def _norm(abbr) -> str:
    return normalize_abbr(str(abbr).strip().upper())


def import_betting_lines(file: str, seasons_from: int = 2024) -> dict:
    """Idempotent import; returns counters (see tests for keys)."""
    import pandas as pd
    job = JobLog(job_name='import-betting-lines',
                 started_at=datetime.now(timezone.utc), status='running')
    db.session.add(job)
    db.session.commit()

    inserted = skipped = matched = unmatched = score_mm = 0
    errors: list[str] = []
    try:
        df = pd.read_csv(file)
        df = df[df['season'] >= seasons_from]

        existing = {(o.game_date, o.home_abbr)
                    for o in HistoricalGameOdds.query.all()}
        # home-side store games for espn match + score cross-check:
        # one representative row per (date, home team)
        store = {}
        for r in HistoricalGameLog.query.filter_by(
                sport='nba', home_away='HOME').all():
            store.setdefault((r.game_date, r.team_abbr), r)

        batch = []
        for rec in df.to_dict('records'):
            try:
                game_date = datetime.strptime(
                    str(rec['date']), '%Y-%m-%d').date()
            except ValueError:
                errors.append(f"bad date: {rec.get('date')!r}")
                continue
            home, away = _norm(rec['home']), _norm(rec['away'])
            if (game_date, home) in existing:
                skipped += 1
                continue
            match = store.get((game_date, home))
            espn_id = match.game_id if match else None
            if match:
                matched += 1
                st = match.stats or {}
                if ('team_score' in st
                        and (float(st['team_score']) != float(rec['score_home'])
                             or float(st.get('opp_score', -1))
                             != float(rec['score_away']))):
                    score_mm += 1
                    logger.warning(
                        "import-betting-lines: score mismatch %s %s",
                        game_date, home)
            else:
                unmatched += 1
            def _f(v):
                try:
                    v = float(v)
                    return None if v != v else v      # NaN -> None
                except (TypeError, ValueError):
                    return None
            batch.append(HistoricalGameOdds(
                game_date=game_date, home_abbr=home, away_abbr=away,
                spread=float(rec['spread']), favored=str(rec['whos_favored']),
                total=float(rec['total']),
                moneyline_home=_f(rec.get('moneyline_home')),
                moneyline_away=_f(rec.get('moneyline_away')),
                is_playoff=bool(rec.get('playoffs')),
                espn_game_id=espn_id,
            ))
            existing.add((game_date, home))
            inserted += 1
        db.session.add_all(batch)
        db.session.commit()
    except Exception as exc:
        db.session.rollback()
        errors.append(str(exc))
        logger.error("import-betting-lines failed: %s", exc)
    finally:
        job.finished_at = datetime.now(timezone.utc)
        job.status = 'failed' if errors else 'success'
        job.message = (f"inserted={inserted} skipped={skipped} "
                       f"matched={matched} unmatched={unmatched} "
                       f"score_mismatches={score_mm}"
                       + (f" errors={'; '.join(errors)}" if errors else ""))
        db.session.commit()
    return {'inserted': inserted, 'skipped': skipped, 'matched': matched,
            'unmatched': unmatched, 'score_mismatches': score_mm,
            'errors': errors}


@click.command('import-betting-lines')
@click.option('--file', 'file_path', required=True)
@click.option('--seasons-from', default=2024, show_default=True, type=int,
              help='Kaggle season end-year floor (2024 = our 2023-24).')
def cli_import_betting_lines(file_path, seasons_from):
    """Import historical closing lines from the Kaggle CSV."""
    result = import_betting_lines(file_path, seasons_from=seasons_from)
    click.echo(
        f"Done: inserted={result['inserted']} skipped={result['skipped']} "
        f"matched={result['matched']} unmatched={result['unmatched']} "
        f"score_mismatches={result['score_mismatches']}"
        + (f" errors={'; '.join(result['errors'])}" if result['errors'] else ""))


def register_odds_import_commands(app):
    app.cli.add_command(cli_import_betting_lines)
```

Register in `app/cli/__init__.py:register_cli` (import + call, same pattern as the other `register_*` pairs).

- [ ] **Step 4: Run tests**

Run: `SECRET_KEY=test python -m unittest tests.test_odds_import -v`
Expected: 5 PASS

- [ ] **Step 5: Lint + commit**

```bash
ruff check . && bandit -q -r app -x tests -ll
git add app/cli/odds_import.py app/cli/__init__.py tests/test_odds_import.py
git commit -m "feat: import-betting-lines CLI — Kaggle closing lines into HistoricalGameOdds"
```

---

### Task 4: Dimension registry + context builder

**Files:**
- Create: `app/services/scenario_dimensions.py`
- Test: `tests/test_scenario_dimensions.py`

**Interfaces:**
- Consumes: pandas; `HistoricalGameLog`, `HistoricalGameOdds` (read-only).
- Produces (Task 5 relies on): `load_frame(sport='nba', seasons: list[str] | None = None) -> pd.DataFrame` (one row per player-game, columns: player_id, player_name, game_id, game_date, season, team_abbr, opp_abbr, home_away, starter + every stats payload key + `pra`); `build_context(df) -> pd.DataFrame` (adds one bucket column per dimension, named `ctx_<dim>`; NaN bucket = excluded); `DIMENSIONS: dict[str, tuple[str, ...]]` mapping dimension name → its bucket labels. Dimension names exactly: `home_away, rest_bucket, role, season_segment, game_script, opp_def_tier, pace_tier, teammate_context, fav_dog, total_bucket`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_scenario_dimensions.py
"""Bucket-function tests over a hand-built mini store."""

from datetime import date

import pandas as pd

from tests.helpers import BaseTestCase


def _mini_frame():
    """4 games, 2 teams (LAL/BOS vs GSW), 3 players; hand-checkable."""
    rows = []
    def add(pid, name, team, opp, gid, gdate, ha, starter, pts, reb, ast,
            fga, fta, tov, minutes, team_score, opp_score):
        rows.append(dict(
            player_id=pid, player_name=name, team_abbr=team, opp_abbr=opp,
            game_id=gid, game_date=gdate, season='2025-26', home_away=ha,
            starter=starter, pts=pts, reb=reb, ast=ast, fg3m=1.0,
            fga=fga, fta=fta, tov=tov, minutes=minutes,
            team_score=team_score, opp_score=opp_score))
    # G1 2025-10-21 LAL(H,120) v GSW(110): margin 10 -> normal
    add('1', 'A', 'LAL', 'GSW', 'g1', date(2025, 10, 21), 'HOME', True,
        30, 8, 9, 20, 8, 3, 36, 120, 110)
    add('2', 'B', 'LAL', 'GSW', 'g1', date(2025, 10, 21), 'HOME', False,
        12, 3, 2, 9, 2, 1, 20, 120, 110)
    add('3', 'C', 'GSW', 'LAL', 'g1', date(2025, 10, 21), 'AWAY', True,
        25, 4, 6, 22, 5, 2, 38, 110, 120)
    # G2 2025-10-22 (b2b for player 1) LAL(A,100) @ GSW(118): blowout 18
    add('1', 'A', 'LAL', 'GSW', 'g2', date(2025, 10, 22), 'AWAY', True,
        22, 7, 7, 18, 6, 4, 34, 100, 118)
    add('3', 'C', 'GSW', 'LAL', 'g2', date(2025, 10, 22), 'HOME', True,
        31, 5, 8, 24, 7, 1, 37, 118, 100)
    # G3 2026-01-15 mid-season, close game margin 3; player 2 absent (teammate ctx)
    add('1', 'A', 'LAL', 'GSW', 'g3', date(2026, 1, 15), 'HOME', True,
        28, 9, 10, 21, 9, 2, 39, 105, 102)
    add('3', 'C', 'GSW', 'LAL', 'g3', date(2026, 1, 15), 'AWAY', True,
        27, 6, 5, 23, 6, 3, 40, 102, 105)
    # G4 2026-03-20 late season
    add('1', 'A', 'LAL', 'GSW', 'g4', date(2026, 3, 20), 'HOME', True,
        35, 10, 11, 25, 10, 2, 41, 130, 112)
    add('2', 'B', 'LAL', 'GSW', 'g4', date(2026, 3, 20), 'HOME', False,
        15, 4, 3, 11, 3, 1, 22, 130, 112)
    add('3', 'C', 'GSW', 'LAL', 'g4', date(2026, 3, 20), 'AWAY', True,
        20, 3, 4, 19, 4, 5, 35, 112, 130)
    return pd.DataFrame(rows)


class TestBuildContext(BaseTestCase):

    def _ctx(self, odds=None):
        from app.services.scenario_dimensions import build_context
        return build_context(_mini_frame(), odds_df=odds)

    def test_pra_home_away_role_segment(self):
        ctx = self._ctx()
        p1g1 = ctx[(ctx.player_id == '1') & (ctx.game_id == 'g1')].iloc[0]
        self.assertEqual(p1g1['pra'], 47.0)              # 30+8+9
        self.assertEqual(p1g1['ctx_home_away'], 'HOME')
        self.assertEqual(p1g1['ctx_role'], 'starter')
        self.assertEqual(p1g1['ctx_season_segment'], 'early')
        p1g3 = ctx[(ctx.player_id == '1') & (ctx.game_id == 'g3')].iloc[0]
        self.assertEqual(p1g3['ctx_season_segment'], 'mid')
        p1g4 = ctx[(ctx.player_id == '1') & (ctx.game_id == 'g4')].iloc[0]
        self.assertEqual(p1g4['ctx_season_segment'], 'late')
        p2 = ctx[(ctx.player_id == '2') & (ctx.game_id == 'g1')].iloc[0]
        self.assertEqual(p2['ctx_role'], 'bench')

    def test_rest_bucket(self):
        ctx = self._ctx()
        g = lambda pid, gid: ctx[(ctx.player_id == pid)
                                 & (ctx.game_id == gid)].iloc[0]
        self.assertEqual(g('1', 'g1')['ctx_rest_bucket'], '3+')   # first game
        self.assertEqual(g('1', 'g2')['ctx_rest_bucket'], '0')    # back-to-back
        self.assertEqual(g('1', 'g3')['ctx_rest_bucket'], '3+')   # months later

    def test_game_script(self):
        ctx = self._ctx()
        g = lambda gid: ctx[(ctx.player_id == '1')
                            & (ctx.game_id == gid)].iloc[0]['ctx_game_script']
        self.assertEqual(g('g1'), 'normal')     # margin 10
        self.assertEqual(g('g2'), 'blowout')    # margin 18
        self.assertEqual(g('g3'), 'close')      # margin 3

    def test_teammate_context(self):
        # player 1's top-2 teammates on LAL = player 2 (only other LAL player)
        ctx = self._ctx()
        g = lambda gid: ctx[(ctx.player_id == '1') & (ctx.game_id == gid)
                            ].iloc[0]['ctx_teammate_context']
        self.assertEqual(g('g1'), 'full')          # player 2 played g1
        self.assertEqual(g('g3'), 'shorthanded')   # player 2 absent g3

    def test_opp_def_tier_is_leakage_safe(self):
        # First game of the season has NO prior data -> NaN bucket (excluded)
        ctx = self._ctx()
        p1g1 = ctx[(ctx.player_id == '1') & (ctx.game_id == 'g1')].iloc[0]
        self.assertTrue(pd.isna(p1g1['ctx_opp_def_tier']))
        # g2: GSW's prior allowed = 120 (g1). With 2 teams the league table
        # is tiny; assert the bucket is one of the labels, not NaN.
        p1g2 = ctx[(ctx.player_id == '1') & (ctx.game_id == 'g2')].iloc[0]
        self.assertFalse(pd.isna(p1g2['ctx_opp_def_tier']))

    def test_fav_dog_and_total_from_odds(self):
        odds = pd.DataFrame([
            dict(game_date=date(2025, 10, 21), home_abbr='LAL',
                 away_abbr='GSW', spread=6.5, favored='home', total=220.0),
            dict(game_date=date(2025, 10, 22), home_abbr='GSW',
                 away_abbr='LAL', spread=8.0, favored='home', total=230.0),
            dict(game_date=date(2026, 1, 15), home_abbr='LAL',
                 away_abbr='GSW', spread=3.0, favored='away', total=210.0),
        ])
        ctx = self._ctx(odds=odds)
        g = lambda pid, gid, col: ctx[(ctx.player_id == pid)
                                      & (ctx.game_id == gid)].iloc[0][col]
        self.assertEqual(g('1', 'g1', 'ctx_fav_dog'), 'fav')      # LAL -6.5 home
        self.assertEqual(g('3', 'g1', 'ctx_fav_dog'), 'dog')
        self.assertEqual(g('1', 'g2', 'ctx_fav_dog'), 'dog_big')  # GSW -8
        self.assertEqual(g('1', 'g3', 'ctx_fav_dog'), 'dog')      # away favored 3
        self.assertTrue(pd.isna(g('1', 'g4', 'ctx_fav_dog')))     # no odds row
        # total buckets: tertiles of [220, 230, 210] within season
        self.assertEqual(g('1', 'g3', 'ctx_total_bucket'), 'low')
        self.assertEqual(g('1', 'g2', 'ctx_total_bucket'), 'high')

    def test_dimensions_registry_shape(self):
        from app.services.scenario_dimensions import DIMENSIONS
        self.assertEqual(len(DIMENSIONS), 10)
        self.assertEqual(DIMENSIONS['fav_dog'],
                         ('fav_big', 'fav', 'dog', 'dog_big'))
```

- [ ] **Step 2: Run to verify failure**

Run: `SECRET_KEY=test python -m unittest tests.test_scenario_dimensions -v`
Expected: ModuleNotFoundError.

- [ ] **Step 3: Implement `app/services/scenario_dimensions.py`**

```python
"""Context builder + dimension registry for the scenario engine.

Each dimension contributes one `ctx_<name>` bucket column to the
player-game frame. NaN bucket = row excluded from that dimension's splits
(e.g. no odds row, no prior defensive data). Buckets are plain strings.
"""

from __future__ import annotations

import pandas as pd

from app import db
from app.models import HistoricalGameLog, HistoricalGameOdds

SPLIT_STATS = ('pts', 'reb', 'ast', 'fg3m', 'pra')

DIMENSIONS: dict[str, tuple[str, ...]] = {
    'home_away': ('HOME', 'AWAY'),
    'rest_bucket': ('0', '1', '2', '3+'),
    'role': ('starter', 'bench'),
    'season_segment': ('early', 'mid', 'late'),
    'game_script': ('close', 'normal', 'blowout'),
    'opp_def_tier': ('top10', 'mid', 'bottom10'),
    'pace_tier': ('slow', 'mid', 'fast'),
    'teammate_context': ('full', 'shorthanded'),
    'fav_dog': ('fav_big', 'fav', 'dog', 'dog_big'),
    'total_bucket': ('low', 'mid', 'high'),
}
# Reserved (not implemented): line_move (Plan D), referee_crew (no free data).


def load_frame(sport: str = 'nba',
               seasons: list[str] | None = None) -> pd.DataFrame:
    """One row per player-game with payload stats flattened + pra."""
    q = HistoricalGameLog.query.filter_by(sport=sport)
    if seasons:
        q = q.filter(HistoricalGameLog.season.in_(seasons))
    records = []
    for r in q.all():
        rec = dict(player_id=r.player_id, player_name=r.player_name,
                   game_id=r.game_id, game_date=r.game_date,
                   season=r.season, team_abbr=r.team_abbr,
                   opp_abbr=r.opp_abbr, home_away=r.home_away,
                   starter=bool(r.starter))
        rec.update(r.stats or {})
        records.append(rec)
    df = pd.DataFrame(records)
    if not df.empty:
        df['pra'] = df['pts'] + df['reb'] + df['ast']
    return df


def load_odds_frame() -> pd.DataFrame:
    rows = [dict(game_date=o.game_date, home_abbr=o.home_abbr,
                 away_abbr=o.away_abbr, spread=o.spread,
                 favored=o.favored, total=o.total)
            for o in HistoricalGameOdds.query.all()]
    return pd.DataFrame(rows)


def _team_games(df: pd.DataFrame) -> pd.DataFrame:
    """One row per (game_id, team): totals needed for pace/def tiers."""
    g = df.groupby(['game_id', 'game_date', 'season', 'team_abbr',
                    'opp_abbr'], as_index=False).agg(
        fga=('fga', 'sum'), fta=('fta', 'sum'), tov=('tov', 'sum'),
        team_score=('team_score', 'first'), opp_score=('opp_score', 'first'))
    return g


def build_context(df: pd.DataFrame,
                  odds_df: pd.DataFrame | None = None) -> pd.DataFrame:
    df = df.copy()
    if 'pra' not in df.columns:
        df['pra'] = df['pts'] + df['reb'] + df['ast']

    # --- simple row-wise dims
    df['ctx_home_away'] = df['home_away']
    df['ctx_role'] = df['starter'].map({True: 'starter', False: 'bench'})
    month = pd.to_datetime(df['game_date']).dt.month
    df['ctx_season_segment'] = pd.cut(
        month.where(month >= 9, month + 12),   # Oct..Apr -> 10..16
        bins=[9, 12, 14, 17], labels=['early', 'mid', 'late']).astype(object)

    # --- game_script from realized margin
    margin = (df['team_score'] - df['opp_score']).abs()
    df['ctx_game_script'] = pd.cut(
        margin, bins=[-1, 5, 14, 10_000],
        labels=['close', 'normal', 'blowout']).astype(object)
    df.loc[df['team_score'].isna() | df['opp_score'].isna(),
           'ctx_game_script'] = float('nan')

    # --- rest per player
    df = df.sort_values(['player_id', 'game_date'])
    gap = df.groupby('player_id')['game_date'].diff().dt.days if \
        pd.api.types.is_datetime64_any_dtype(df['game_date']) else \
        df.groupby('player_id')['game_date'].diff().map(
            lambda d: d.days if pd.notna(d) else None)
    rest = gap - 1
    df['ctx_rest_bucket'] = pd.cut(
        rest.fillna(99), bins=[-1, 0, 1, 2, 10_000],
        labels=['0', '1', '2', '3+']).astype(object)

    # --- team-game table for pace + def tiers
    tg = _team_games(df)
    # pace: both teams' possession estimates summed per game
    tg['poss'] = tg['fga'] + 0.44 * tg['fta'] + tg['tov']
    game_poss = tg.groupby('game_id', as_index=False).agg(
        season=('season', 'first'), poss=('poss', 'sum'))
    game_poss['ctx_pace_tier'] = game_poss.groupby('season')['poss'].transform(
        lambda s: pd.qcut(s, 3, labels=['slow', 'mid', 'fast'],
                          duplicates='drop').astype(object))
    df = df.merge(game_poss[['game_id', 'ctx_pace_tier']],
                  on='game_id', how='left')

    # --- opp_def_tier: opponent's PRIOR season-to-date points allowed,
    # ranked cross-sectionally among all teams' priors as of that date.
    allowed = tg[['game_id', 'game_date', 'season', 'team_abbr',
                  'opp_score']].rename(columns={'opp_score': 'allowed'})
    allowed = allowed.sort_values(['season', 'team_abbr', 'game_date'])
    allowed['prior_allowed'] = allowed.groupby(
        ['season', 'team_abbr'])['allowed'].transform(
        lambda s: s.expanding().mean().shift(1))
    # rank each team's prior among teams with data in the same season+date
    def _tier(group):
        pct = group['prior_allowed'].rank(pct=True)
        return pd.cut(pct, bins=[0, 1 / 3, 2 / 3, 1.0001],
                      labels=['top10', 'mid', 'bottom10']).astype(object)
    allowed['def_tier'] = allowed.groupby(
        ['season', 'game_date'], group_keys=False).apply(_tier)
    allowed.loc[allowed['prior_allowed'].isna(), 'def_tier'] = float('nan')
    df = df.merge(
        allowed[['game_id', 'team_abbr', 'def_tier']].rename(
            columns={'team_abbr': 'opp_abbr',
                     'def_tier': 'ctx_opp_def_tier'}),
        on=['game_id', 'opp_abbr'], how='left')

    # --- teammate_context: top-2 teammates by minutes-weighted usage
    df['_wusage'] = df.get('usage_pct', 0.0) * df['minutes']
    top2 = (df.groupby(['season', 'team_abbr', 'player_id'])['_wusage']
              .sum().reset_index()
              .sort_values(['season', 'team_abbr', '_wusage'],
                           ascending=[True, True, False]))
    top2['rank'] = top2.groupby(['season', 'team_abbr']).cumcount()
    key_players = top2[top2['rank'] < 3]        # top-3 pool; teammates = top-2 excl self
    key_by_team = key_players.groupby(['season', 'team_abbr'])[
        'player_id'].apply(list).to_dict()
    present = df.groupby(['game_id', 'team_abbr'])['player_id'].apply(set)
    def _teammate_bucket(row):
        keys = key_by_team.get((row['season'], row['team_abbr']), [])
        mates = [p for p in keys if p != row['player_id']][:2]
        if not mates:
            return float('nan')
        there = present.get((row['game_id'], row['team_abbr']), set())
        return 'full' if all(m in there for m in mates) else 'shorthanded'
    df['ctx_teammate_context'] = df.apply(_teammate_bucket, axis=1)
    df = df.drop(columns=['_wusage'])

    # --- odds dims
    if odds_df is None or odds_df.empty:
        df['ctx_fav_dog'] = float('nan')
        df['ctx_total_bucket'] = float('nan')
        return df
    o = odds_df.copy()
    o['season_key'] = o['game_date'].map(
        lambda d: f"{d.year}-{str(d.year + 1)[-2:]}" if d.month >= 10
        else f"{d.year - 1}-{str(d.year)[-2:]}")
    o['ctx_total_bucket'] = o.groupby('season_key')['total'].transform(
        lambda s: pd.qcut(s, 3, labels=['low', 'mid', 'high'],
                          duplicates='drop').astype(object))
    # join twice: once as home team, once as away
    for side, other in (('home_abbr', 'away_abbr'), ('away_abbr', 'home_abbr')):
        sub = o[['game_date', side, 'spread', 'favored',
                 'ctx_total_bucket']].rename(columns={side: 'team_abbr'})
        sub['is_home_side'] = side == 'home_abbr'
        if side == 'home_abbr':
            merged_home = sub
        else:
            merged_away = sub
    odds_long = pd.concat([merged_home, merged_away], ignore_index=True)
    def _fav_bucket(row):
        team_favored = ((row['favored'] == 'home') == row['is_home_side'])
        big = abs(row['spread']) > 7
        if row['spread'] == 0:
            return 'fav'
        if team_favored:
            return 'fav_big' if big else 'fav'
        return 'dog_big' if big else 'dog'
    odds_long['ctx_fav_dog'] = odds_long.apply(_fav_bucket, axis=1)
    df = df.merge(
        odds_long[['game_date', 'team_abbr', 'ctx_fav_dog',
                   'ctx_total_bucket']],
        on=['game_date', 'team_abbr'], how='left')
    return df
```

NOTE for the implementer: `game_date` arrives as `datetime.date` objects from SQLAlchemy; the mini-frame fixture also uses `date` objects. `pd.to_datetime(df['game_date'])` handles both. Verify the rest-gap branch that actually executes with date objects and delete the dead branch (keep whichever `diff()` form works against the fixture — run the test to find out, then simplify to that single form).

- [ ] **Step 4: Run tests**

Run: `SECRET_KEY=test python -m unittest tests.test_scenario_dimensions -v`
Expected: 8 PASS

- [ ] **Step 5: Lint + commit**

```bash
ruff check . && bandit -q -r app -x tests -ll
git add app/services/scenario_dimensions.py tests/test_scenario_dimensions.py
git commit -m "feat: scenario dimension registry + vectorized context builder"
```

---

### Task 5: Scenario engine — shrinkage, splits, refresh, agreement score

**Files:**
- Create: `app/services/scenario_engine.py`
- Test: `tests/test_scenario_engine.py`

**Interfaces:**
- Consumes (Task 4): `load_frame`, `load_odds_frame`, `build_context`, `DIMENSIONS`, `SPLIT_STATS`.
- Produces (Task 6): `refresh_splits(sport='nba') -> dict` (`{'players': int, 'rows': int, 'skipped_reason': str | None}`) — full DELETE+INSERT; skips (returns skipped_reason='no_new_data') when `max(HistoricalGameLog.fetched_at)` predates the last successful 'refresh-scenario-splits' JobLog; always writes a JobLog. `fit_prior_strength(df, stat) -> float`; `agreement_score(player_id, stat, line, context, sport='nba') -> tuple[float, int]`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_scenario_engine.py
"""Engine math + end-to-end materialization tests."""

from datetime import date, datetime, timezone
from unittest.mock import patch

import pandas as pd

from tests.helpers import BaseTestCase
from tests.test_scenario_dimensions import _mini_frame


class TestPriorStrength(BaseTestCase):

    def test_k_fit_method_of_moments_and_clamps(self):
        from app.services.scenario_engine import fit_prior_strength
        # two players, wildly different means, low within-noise -> small k
        df = pd.DataFrame({
            'player_id': ['1'] * 4 + ['2'] * 4,
            'pts': [30, 31, 29, 30, 10, 11, 9, 10],
        })
        k_small = fit_prior_strength(df, 'pts')
        self.assertEqual(k_small, 2.0)              # clamped at floor
        # identical means, pure noise -> k at cap
        df2 = pd.DataFrame({
            'player_id': ['1'] * 4 + ['2'] * 4,
            'pts': [10, 30, 20, 40, 25, 5, 35, 15],
        })
        self.assertEqual(fit_prior_strength(df2, 'pts'), 25.0)

    def test_shrunk_mean_formula(self):
        from app.services.scenario_engine import shrink
        # (n*raw + k*baseline) / (n+k): (4*20 + 6*10) / 10 = 14
        self.assertAlmostEqual(shrink(raw=20.0, n=4, baseline=10.0, k=6.0),
                               14.0)


class TestRefreshSplits(BaseTestCase):

    def _seed_store(self):
        """Persist the mini frame as HistoricalGameLog rows (>=15-game gate
        disabled via min_games param)."""
        from app import db
        from app.models import HistoricalGameLog
        for rec in _mini_frame().to_dict('records'):
            stats = {k: float(rec[k]) for k in
                     ('pts', 'reb', 'ast', 'fg3m', 'fga', 'fta', 'tov',
                      'minutes', 'team_score', 'opp_score')}
            stats['usage_pct'] = 0.2
            db.session.add(HistoricalGameLog(
                sport='nba', player_id=rec['player_id'],
                player_name=rec['player_name'], team_abbr=rec['team_abbr'],
                opp_abbr=rec['opp_abbr'], game_id=rec['game_id'],
                game_date=rec['game_date'], season=rec['season'],
                home_away=rec['home_away'], win_loss='W',
                starter=rec['starter'], stats=stats))
        db.session.commit()

    def test_end_to_end_materialization(self):
        from app.models import JobLog, ScenarioSplit
        from app.services.scenario_engine import refresh_splits
        with self.app.app_context():
            self._seed_store()
            result = refresh_splits(sport='nba', min_games=1)
            self.assertGreater(result['rows'], 0)
            self.assertEqual(result['players'], 3)
            # player 1 HOME pts: games g1,g3,g4 -> raw mean (30+28+35)/3=31
            row = ScenarioSplit.query.filter_by(
                player_id='1', stat='pts', dim1='home_away', bucket1='HOME',
                dim2=None, season_scope='all').one()
            self.assertEqual(row.n, 3)
            self.assertAlmostEqual(row.raw_mean, 31.0)
            # shrunk sits strictly between raw and baseline
            self.assertTrue(min(row.raw_mean, row.baseline_mean)
                            <= row.shrunk_mean
                            <= max(row.raw_mean, row.baseline_mean))
            job = JobLog.query.filter_by(
                job_name='refresh-scenario-splits').one()
            self.assertEqual(job.status, 'success')

    def test_refresh_replaces_not_duplicates(self):
        from app.models import ScenarioSplit
        from app.services.scenario_engine import refresh_splits
        with self.app.app_context():
            self._seed_store()
            refresh_splits(sport='nba', min_games=1)
            first = ScenarioSplit.query.count()
            refresh_splits(sport='nba', min_games=1, force=True)
            self.assertEqual(ScenarioSplit.query.count(), first)

    def test_no_new_data_guard(self):
        from app.services.scenario_engine import refresh_splits
        with self.app.app_context():
            self._seed_store()
            refresh_splits(sport='nba', min_games=1)
            result = refresh_splits(sport='nba', min_games=1)   # no force
            self.assertEqual(result['skipped_reason'], 'no_new_data')

    def test_n_below_3_not_stored(self):
        from app.models import ScenarioSplit
        from app.services.scenario_engine import refresh_splits
        with self.app.app_context():
            self._seed_store()
            refresh_splits(sport='nba', min_games=1)
            self.assertEqual(
                ScenarioSplit.query.filter(ScenarioSplit.n < 3).count(), 0)


class TestAgreementScore(BaseTestCase):

    def test_signed_weighted_agreement(self):
        from app import db
        from app.models import ScenarioSplit
        from app.services.scenario_engine import agreement_score
        with self.app.app_context():
            def split(dim1, b1, shrunk, n, dim2=None, b2=None):
                db.session.add(ScenarioSplit(
                    sport='nba', player_id='1', player_name='A', stat='pts',
                    dim1=dim1, bucket1=b1, dim2=dim2, bucket2=b2,
                    season_scope='all', n=n, raw_mean=shrunk,
                    shrunk_mean=shrunk, baseline_mean=25.0))
            split('home_away', 'HOME', 30.0, 10)     # over 25.5, w10
            split('rest_bucket', '0', 28.0, 5)       # over, w5
            split('home_away', 'HOME', 22.0, 5,
                  dim2='rest_bucket', b2='0')        # under, w5
            db.session.commit()
            score, n_splits = agreement_score(
                '1', 'pts', 25.5,
                {'home_away': 'HOME', 'rest_bucket': '0'})
            self.assertEqual(n_splits, 3)
            # (10 + 5 - 5) / 20 = +0.5
            self.assertAlmostEqual(score, 0.5)

    def test_no_matching_splits(self):
        from app.services.scenario_engine import agreement_score
        with self.app.app_context():
            score, n = agreement_score('9', 'pts', 20.0,
                                       {'home_away': 'AWAY'})
            self.assertEqual((score, n), (0.0, 0))
```

- [ ] **Step 2: Run to verify failure**

Run: `SECRET_KEY=test python -m unittest tests.test_scenario_engine -v`
Expected: ModuleNotFoundError.

- [ ] **Step 3: Implement `app/services/scenario_engine.py`**

```python
"""Scenario engine: shrunk conditional splits + agreement score (Plan B).

refresh_splits() is the nightly materialization: load store -> context ->
per (player, stat) singles + pairwise groupbys -> empirical-Bayes shrink ->
DELETE+INSERT ScenarioSplit. Derived data only.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from itertools import combinations

from app import db
from app.models import HistoricalGameLog, JobLog, ScenarioSplit
from app.services.scenario_dimensions import (
    DIMENSIONS, SPLIT_STATS, build_context, load_frame, load_odds_frame,
)

logger = logging.getLogger(__name__)

K_FLOOR, K_CAP = 2.0, 25.0
MIN_N = 3
MIN_GAMES_DEFAULT = 15


def shrink(raw: float, n: int, baseline: float, k: float) -> float:
    return (n * raw + k * baseline) / (n + k)


def fit_prior_strength(df, stat: str) -> float:
    """Method-of-moments k: within-player variance / between-player
    variance of per-game values. Noisy stat + similar players -> big k."""
    grouped = df.groupby('player_id')[stat]
    within = grouped.var(ddof=1).mean()
    between = grouped.mean().var(ddof=1)
    if not between or between != between or not within or within != within:
        return K_CAP
    k = within / between
    return float(min(max(k, K_FLOOR), K_CAP))


def _last_success_utc() -> datetime | None:
    job = (JobLog.query.filter_by(job_name='refresh-scenario-splits',
                                  status='success')
           .order_by(JobLog.finished_at.desc()).first())
    return job.finished_at if job else None


def refresh_splits(sport: str = 'nba', min_games: int = MIN_GAMES_DEFAULT,
                   force: bool = False) -> dict:
    job = JobLog(job_name='refresh-scenario-splits',
                 started_at=datetime.now(timezone.utc), status='running')
    db.session.add(job)
    db.session.commit()
    players = rows_written = 0
    skipped_reason = None
    try:
        if not force:
            last = _last_success_utc()
            newest = db.session.query(
                db.func.max(HistoricalGameLog.fetched_at)).filter_by(
                sport=sport).scalar()
            if last is not None and newest is not None and \
                    _naive(newest) <= _naive(last):
                skipped_reason = 'no_new_data'
                return {'players': 0, 'rows': 0,
                        'skipped_reason': skipped_reason}

        df = load_frame(sport=sport)
        if df.empty:
            skipped_reason = 'empty_store'
            return {'players': 0, 'rows': 0, 'skipped_reason': skipped_reason}
        ctx = build_context(df, odds_df=load_odds_frame())

        # gate: >= min_games in the trailing 2 seasons
        seasons = sorted(ctx['season'].unique())[-2:]
        scope_all = ctx[ctx['season'].isin(seasons)]
        counts = scope_all.groupby('player_id')['game_id'].nunique()
        eligible = set(counts[counts >= min_games].index)
        players = len(eligible)

        ks = {stat: fit_prior_strength(scope_all, stat)
              for stat in SPLIT_STATS}
        current = seasons[-1]
        batch = []
        for scope_name, frame in (('all', scope_all),
                                  (current,
                                   scope_all[scope_all['season'] == current])):
            frame = frame[frame['player_id'].isin(eligible)]
            names = {pid: n for pid, n in
                     frame.groupby('player_id')['player_name'].first().items()}
            baselines = frame.groupby('player_id')[list(SPLIT_STATS)].mean()
            dims = list(DIMENSIONS)
            combos = ([(d, None) for d in dims]
                      + [(a, b) for a, b in combinations(dims, 2)])
            for dim1, dim2 in combos:
                cols = ['player_id', f'ctx_{dim1}'] + (
                    [f'ctx_{dim2}'] if dim2 else [])
                sub = frame.dropna(subset=cols[1:])
                if sub.empty:
                    continue
                agg = sub.groupby(cols)[list(SPLIT_STATS)].agg(
                    ['mean', 'count'])
                for key, row in agg.iterrows():
                    pid = key[0]
                    b1 = str(key[1])
                    b2 = str(key[2]) if dim2 else None
                    for stat in SPLIT_STATS:
                        n = int(row[(stat, 'count')])
                        if n < MIN_N:
                            continue
                        raw = float(row[(stat, 'mean')])
                        base = float(baselines.loc[pid, stat])
                        batch.append(dict(
                            sport=sport, player_id=pid,
                            player_name=names[pid], stat=stat,
                            dim1=dim1, bucket1=b1, dim2=dim2, bucket2=b2,
                            season_scope=scope_name, n=n, raw_mean=raw,
                            shrunk_mean=shrink(raw, n, base, ks[stat]),
                            baseline_mean=base,
                            computed_at=datetime.now(timezone.utc)))
        ScenarioSplit.query.filter_by(sport=sport).delete()
        if batch:
            db.session.bulk_insert_mappings(ScenarioSplit, batch)
        db.session.commit()
        rows_written = len(batch)
        return {'players': players, 'rows': rows_written,
                'skipped_reason': None}
    except Exception as exc:
        db.session.rollback()
        skipped_reason = f'error: {exc}'
        logger.error("refresh-scenario-splits failed: %s", exc)
        raise
    finally:
        job.finished_at = datetime.now(timezone.utc)
        job.status = ('success' if skipped_reason in (None, 'no_new_data',
                                                      'empty_store')
                      else 'failed')
        job.message = (f"players={players} rows={rows_written}"
                       + (f" skipped={skipped_reason}" if skipped_reason
                          else ""))
        db.session.commit()


def _naive(dt: datetime) -> datetime:
    return dt.replace(tzinfo=None) if dt.tzinfo else dt


def agreement_score(player_id: str, stat: str, line: float,
                    context: dict, sport: str = 'nba') -> tuple[float, int]:
    """Signed weighted share of applicable splits vs the line (+ = over)."""
    q = ScenarioSplit.query.filter_by(
        sport=sport, player_id=str(player_id), stat=stat,
        season_scope='all')
    matches = []
    for s in q.all():
        if s.dim1 not in context or context[s.dim1] != s.bucket1:
            continue
        if s.dim2 is not None and (
                s.dim2 not in context or context[s.dim2] != s.bucket2):
            continue
        matches.append(s)
    if not matches:
        return 0.0, 0
    total_w = sum(s.n for s in matches)
    signed = sum(s.n * (1 if s.shrunk_mean > line else -1) for s in matches)
    return signed / total_w, len(matches)
```

- [ ] **Step 4: Run tests**

Run: `SECRET_KEY=test python -m unittest tests.test_scenario_engine -v`
Expected: 8 PASS. (If the JobLog `finished_at` ordering or tz-naive comparison trips the guard test, the `_naive` helper is the intended fix point — both datetimes must compare naive-UTC.)

- [ ] **Step 5: Lint + commit**

```bash
ruff check . && bandit -q -r app -x tests -ll
git add app/services/scenario_engine.py tests/test_scenario_engine.py
git commit -m "feat: scenario engine — EB-shrunk splits, nightly refresh core, agreement score"
```

---

### Task 6: Wiring — scheduler job + CLIs

**Files:**
- Create: `app/cli/scenario_commands.py`
- Modify: `app/cli/__init__.py`, `app/services/scheduler.py`
- Test: append to `tests/test_scenario_engine.py`

**Interfaces:**
- Consumes: `refresh_splits` (Task 5); `ScenarioSplit` model.
- Produces: scheduler job id `refresh_scenario_splits` (CronTrigger hour=5, minute=10, ET, `_log_job` wrapper); CLIs `flask refresh-splits [--sport nba] [--force]` and `flask show-splits --player NAME --stat pts [--dim DIM]`.

- [ ] **Step 1: Write the failing tests** (append to tests/test_scenario_engine.py)

```python
class TestWiring(BaseTestCase):

    @patch('app.services.scenario_engine.refresh_splits',
           return_value={'players': 2, 'rows': 40, 'skipped_reason': None})
    def test_refresh_cli(self, mock_refresh):
        runner = self.app.test_cli_runner()
        result = runner.invoke(args=['refresh-splits', '--force'])
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIn('rows=40', result.output)
        mock_refresh.assert_called_once_with(sport='nba', force=True)

    def test_show_splits_cli(self):
        from app import db
        from app.models import ScenarioSplit
        with self.app.app_context():
            db.session.add(ScenarioSplit(
                sport='nba', player_id='1', player_name='LeBron James',
                stat='pts', dim1='home_away', bucket1='HOME', dim2=None,
                bucket2=None, season_scope='all', n=41, raw_mean=27.1,
                shrunk_mean=26.8, baseline_mean=26.2))
            db.session.commit()
        runner = self.app.test_cli_runner()
        result = runner.invoke(args=['show-splits', '--player',
                                     'LeBron James', '--stat', 'pts'])
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIn('home_away=HOME', result.output)
        self.assertIn('26.8', result.output)

    def test_scheduler_job_registered(self):
        import re
        from pathlib import Path
        src = Path('app/services/scheduler.py').read_text()
        self.assertIn("id='refresh_scenario_splits'", src)
        self.assertIn("hour=5, minute=10", src)
```

- [ ] **Step 2: Run to verify failure** — `SECRET_KEY=test python -m unittest tests.test_scenario_engine.TestWiring -v` → FAIL/ERROR.

- [ ] **Step 3: Implement `app/cli/scenario_commands.py`**

```python
"""CLIs for the scenario engine (manual refresh + split inspection)."""

import click


@click.command('refresh-splits')
@click.option('--sport', default='nba', show_default=True)
@click.option('--force', is_flag=True, default=False,
              help='Refresh even when the store has no new rows.')
def cli_refresh_splits(sport, force):
    """Recompute and materialize all scenario splits."""
    from app.services.scenario_engine import refresh_splits
    result = refresh_splits(sport=sport, force=force)
    click.echo(f"Done: players={result['players']} rows={result['rows']}"
               + (f" skipped={result['skipped_reason']}"
                  if result['skipped_reason'] else ""))


@click.command('show-splits')
@click.option('--player', required=True)
@click.option('--stat', default='pts', show_default=True)
@click.option('--dim', default=None, help='Filter to one dimension.')
def cli_show_splits(player, stat, dim):
    """Print a player's materialized splits (single-dim rows)."""
    from app.models import ScenarioSplit
    q = ScenarioSplit.query.filter_by(player_name=player, stat=stat,
                                      season_scope='all', dim2=None)
    if dim:
        q = q.filter_by(dim1=dim)
    rows = q.order_by(ScenarioSplit.dim1, ScenarioSplit.bucket1).all()
    if not rows:
        click.echo("no splits found")
        return
    click.echo(f"{player} — {stat} (baseline {rows[0].baseline_mean:.1f})")
    for r in rows:
        click.echo(f"  {r.dim1}={r.bucket1:<12} n={r.n:<4} "
                   f"raw={r.raw_mean:.1f} shrunk={r.shrunk_mean:.1f}")


def register_scenario_commands(app):
    app.cli.add_command(cli_refresh_splits)
    app.cli.add_command(cli_show_splits)
```

Register in `app/cli/__init__.py`. In `app/services/scheduler.py`, add next to the other Plan A2 jobs:

```python
def _run_refresh_scenario_splits():
    from app.services.scenario_engine import refresh_splits
    app = _get_app()
    with app.app_context():
        result = refresh_splits()
        logger.info("scenario splits refresh: %s", result)
```

and in `init_scheduler(app)`:

```python
    # Plan B: nightly scenario-split materialization (after night's appends)
    scheduler.add_job(
        lambda: _log_job('refresh_scenario_splits',
                         _run_refresh_scenario_splits),
        CronTrigger(hour=5, minute=10, timezone=APP_TIMEZONE),
        id='refresh_scenario_splits',
        replace_existing=True,
    )
```

Also update the exact job-count assertion in `tests/test_services.py` (currently 20 → 21) — that is an expected, in-scope edit.

- [ ] **Step 4: Run the module suite** — `SECRET_KEY=test python -m unittest tests.test_scenario_engine -v` → all PASS.

- [ ] **Step 5: Lint + commit**

```bash
ruff check . && bandit -q -r app -x tests -ll
git add app/cli/scenario_commands.py app/cli/__init__.py \
        app/services/scheduler.py tests/test_scenario_engine.py \
        tests/test_services.py
git commit -m "feat: wire scenario refresh job (05:10 ET) + refresh-splits/show-splits CLIs"
```

---

### Task 7: Full verification + scratch end-to-end + docs

**Files:**
- Modify: `CLAUDE.md` (job count line), spec status line.

**Interfaces:** none — verification.

- [ ] **Step 1: Full suite + coverage, foreground**

Run: `SECRET_KEY=test python -m coverage run -m unittest discover -s tests` then `python -m coverage report --include="app/*"`.
Expected: OK (≈1060+ tests), total ≥ 80%.

- [ ] **Step 2: Lint** — `ruff check . && bandit -q -r app -x tests -ll` → clean.

- [ ] **Step 3: Scratch end-to-end dry run (NO touching instance/app.db)**

```bash
export SECRET_KEY=test DATABASE_URL=sqlite:////tmp/planb_e2e.db
rm -f /tmp/planb_e2e.db
python -c "from app import create_app, db; app=create_app();\
  app.app_context().push(); db.create_all(); print('schema ok')"
flask --app run.py db stamp heads -d migrations
flask --app run.py import-hoopr-logs --seasons 1 --max-games 30
flask --app run.py import-betting-lines --file ~/.cache/kagglehub/datasets/cviaxmiwnptr/nba-betting-data-october-2007-to-june-2024/versions/4/nba_2008-2026.csv
flask --app run.py refresh-splits --force
flask --app run.py show-splits --player "LeBron James" --stat pts
unset DATABASE_URL
```

Expected: import reports rows; betting-lines reports matched>0 for the 30 games; refresh reports players>0 rows>0; show-splits prints home/away rows with sane values (pts baseline 20-35 for LeBron). This step downloads from GitHub (hoopR) — the only network step. Record outputs in the task report.

- [ ] **Step 4: Docs** — CLAUDE.md scheduler line → `- Scheduler has 21 registered jobs as of <today> (refresh_scenario_splits added in Plan B)`; spec Status line → `Status: implemented (this plan)`.

- [ ] **Step 5: Commit**

```bash
git add CLAUDE.md docs/superpowers/specs/2026-07-10-plan-b-scenario-engine-design.md
git commit -m "docs: Plan B implemented — job count + spec status"
```

---

## Post-merge operational runbook (controller, after finishing-a-development-branch)

1. `flask --app run.py import-hoopr-logs --seasons 3 --update-stats` (backfills scores into the 79,603 rows).
2. `flask --app run.py import-betting-lines --file <kaggle csv path>` (expect matched ≈ 3,690, unmatched ≈ small: Cup finals + any playoff rows without store games; score_mismatches ≈ 0).
3. Archive the CSV: `cp <csv> ~/claude_brain/Claude-brain/raw/data/kaggle/`.
4. `flask --app run.py refresh-splits --force`, then `show-splits --player "LeBron James" --stat pts` spot-check vs known home/away splits.

## Self-Review (performed at write time)

- **Spec coverage:** scores enrichment + --update-stats (T1), models + migration incl. index drop (T2), odds import + join report + cross-check (T3), 10 dimensions incl. leakage-safe opp_def_tier and NaN-exclusion (T4), shrinkage/k-fit/gating/two scopes/DELETE+INSERT/no-change guard/agreement score (T5), job #21 + CLIs (T6), verification + operational runbook (T7 + post-merge).
- **Placeholder scan:** clean; two intentional adjust-to-reality notes (rest-gap dead-branch cleanup in T4; JobLog tz comparison in T5) direct the implementer to resolve against running code.
- **Type consistency:** `refresh_splits(sport, min_games, force) -> dict` keys match T6 CLI + tests; `agreement_score(...) -> tuple[float, int]` matches tests; DIMENSIONS names in T4 match T5 combos and T6 output; `import_betting_lines` result keys match its CLI echo and tests.
