# Plan B: Scenario Engine — Conditional Splits over HistoricalGameLog (Design)

Date: 2026-07-10
Status: implemented (this plan)
Predecessors: Plan A (79,603-row HistoricalGameLog, ESPN id namespace) and
Plan A2 (game-day coordinator keeps the store current) — both merged to main.

## Goal

Compute statistically honest conditional performance splits per (player,
stat) across 11 context dimensions over HistoricalGameLog, materialize them
nightly into a `ScenarioSplit` table for instant reads, and expose an
agreement score — the feature surface Plan C's distributional model will
consume and Phase 2's UI will browse. Engine only: no FEATURE_KEYS, model,
or UI changes in this plan.

## Constraints (environment truths)

- Data store: main SQLite `instance/app.db`; HistoricalGameLog is
  regular-season-only, ESPN id namespace, 2023-24..2025-26 (~79.6k rows,
  1,231 games/season incl. NBA Cup finals), kept current by the Plan A2
  coordinator + weekly hoopR reconcile.
- HistoricalGameLog.stats currently lacks team/opp scores — required for
  the game_script dimension. Fixed in this plan (see Data enrichment).
- Historical betting lines: Kaggle dataset `cviaxmiwnptr/nba-betting-data-
  october-2007-to-june-2024` (file nba_2008-2026.csv, 24,440 games,
  2007-10-30 → 2026-06-13). VALIDATED 2026-07-10: 100.0% join rate against
  our store on (date, home team) for all 3,690 overlapping regular-season
  games; spread/total complete; moneylines missing in recent seasons (not
  needed — favorite status derives from spread); team abbrs are ESPN-style
  aliases already handled by `espn_mapping`. The 3-game count difference
  vs our store = NBA Cup finals (absent from Kaggle's regular flag; our
  store includes them — odds dims are simply NULL-context for those 3).
- No opening-vs-closing line pairs anywhere yet → `line_move` dimension
  DEFERRED to Plan D (closing-line work). 2026-27 odds context accrues
  organically via the coordinator's GameSnapshot odds — no future Kaggle
  dependency.
- pandas 2.2.3 available; 79.6k rows trivially fit memory. All date logic
  ET. unittest runner; ruff+bandit gates; foreground test runs; no
  Co-Authored-By in commits.

## Part 1: Data enrichment (prerequisite tasks)

### 1a. Team scores into stats payload

- Both import paths gain `team_score`/`opp_score` (floats) in the stats
  JSON: `hoopr_import` (columns team_score/opponent_team_score) and
  `espn_history_append` (from the scoreboard game dict it already receives).
- `import-hoopr-logs` gains `--update-stats`: for existing (player_id,
  game_id) rows, merge MISSING payload keys only (never overwrite present
  values); insert-new behavior unchanged. One re-run over 3 seasons
  backfills scores (~1 min, GitHub download, free).

### 1b. HistoricalGameOdds table + importer

New model `HistoricalGameOdds`:
`id, game_date (Date, indexed), home_abbr (String 10), away_abbr (String
10), spread (Float, positive number = favorite's margin), favored
(String 4: 'home'|'away'), total (Float), moneyline_home (Float, nullable),
moneyline_away (Float, nullable), is_playoff (Boolean), source (String 20,
default 'kaggle'), espn_game_id (String 30, nullable — filled when a store
game matches on date+home)`. Unique constraint (game_date, home_abbr).
Abbrs stored NBA-normalized (via espn_mapping.normalize_abbr on upper()).

New CLI `flask import-betting-lines --file PATH [--seasons-from 2024]`:
reads the Kaggle CSV, normalizes abbrs, filters to regular season by
default (playoff rows imported too but flagged is_playoff — cheap, and
Plan C may want them), idempotent on the unique key, matches espn_game_id
against HistoricalGameLog home games, and echoes a join-rate report
(matched/unmatched counts; unmatched games listed). JobLog row per run
('import-betting-lines'). Source CSV archived to the brain vault
(`raw/data/kaggle/nba_2008-2026.csv`) at import time for provenance.

Cross-validation built into the importer: where both sides exist, compare
Kaggle score_home/score_away against the freshly enriched stats payload
scores; mismatches are reported (expected ≈ 0, tolerance: report-only).

### Migration

One Alembic migration adds ScenarioSplit + HistoricalGameOdds AND drops the
redundant `ix_historical_game_log_sport` single-column index (deferred
follow-up from Plan A — subset of both composite indexes).

## Part 2: The engine

### Dimensions (10 split dimensions + a sample gate; registry-driven)

`app/services/scenario_dimensions.py` — a DIMENSIONS registry; each entry:
name, bucket labels, and a vectorized function frame → bucket series.
Game-level context columns are precomputed once per engine run.

1. home_away: HOME | AWAY (column).
2. rest_bucket: 0 | 1 | 2 | 3+ days (per-player game_date gaps; first game
   of season = 3+).
3. role: starter | bench (starter column).
4. season_segment: early (Oct–Dec) | mid (Jan–Feb) | late (Mar–Apr).
5. game_script: close (final margin ≤ 5) | normal (6–14) | blowout (≥ 15),
   from the enriched team/opp scores.
6. opp_def_tier: top10 | mid | bottom10 — opponent's season-to-date points
   allowed per game, computed from the store itself (leakage-safe: uses
   only games BEFORE the row's game_date).
7. pace_tier: slow | mid | fast — tertiles of game possession estimate
   (team-level FGA + 0.44·FTA + TOV summed over both teams), per season.
8. teammate_context: full | shorthanded — top-2 usage teammates (by
   season minutes-weighted usage) both have rows that game vs not.
9. fav_dog: fav_big (player's team favored by >7) | fav (favored by ≤7,
   incl. pick'em spread 0 for both teams) | dog (underdog by ≤7) |
   dog_big (underdog by >7) — from HistoricalGameOdds spread/favored
   joined on (game_date, home_abbr); rows without odds get bucket NULL
   (excluded from this dimension's splits, counted in coverage stats).
10. total_bucket: low | mid | high — per-season tertiles of the O/U total.
11. min_gate (not a split dimension): players enter the engine only with
    ≥ 15 games in the trailing 2 seasons.

Reserved (registry slots documented, not implemented): line_move (Plan D),
referee crew (spec'd best-effort/deferred in the Phase 1 design).

### ScenarioSplit table

`id, sport (String 10, default 'nba'), player_id (String 20), player_name
(String 120), stat (String 20), dim1 (String 30), bucket1 (String 20),
dim2 (String 30, nullable), bucket2 (String 20, nullable), season_scope
(String 10: 'all' | season string), n (Integer), raw_mean (Float),
shrunk_mean (Float), baseline_mean (Float), computed_at (DateTime UTC)`.
Unique constraint (sport, player_id, stat, dim1, bucket1, dim2, bucket2,
season_scope); index (sport, player_id, stat).

### Engine (`app/services/scenario_engine.py`)

- Load store once into a DataFrame; compute context columns; per (player,
  stat in SPORT_STAT_CONFIG's split stats: pts, reb, ast, fg3m, pra):
  groupby singles + all C(10,2) pairwise combos of the 10 split dimensions.
- Empirical Bayes shrinkage: `shrunk = (n·raw + k·baseline) / (n + k)`;
  baseline = player's overall mean for the stat in scope; per-stat prior
  strength k fit from league between-player variance (method-of-moments;
  computed once per run; k floor 2, cap 25).
- Store splits with n ≥ 3 only. season_scope rows: 'all' (trailing 2
  seasons) and current season.
- Full-refresh semantics: DELETE + bulk INSERT inside one transaction per
  (sport) run — the table is derived data, never hand-edited.
- Volume estimate: ~450 gated players × 5 stats × ~200 combos × 2 scopes
  ≈ 0.4–0.9M rows; SQLite fine with the lookup index; run time minutes.

### Agreement score

`agreement_score(player_id, stat, line, context: dict) -> (score, n_splits)`
— context maps dimension → tonight's bucket; pulls matching single +
pairwise splits (season_scope 'all'), each votes over/under vs the line by
its shrunk_mean, weighted by n; score = weighted share in the majority
direction, signed (+ = over). Pure read function; Plan C consumes it.

### Materialization + access

- Scheduler job #21 `refresh_scenario_splits`, nightly 05:10 ET, guarded:
  skip when HistoricalGameLog max(fetched_at) predates the last successful
  refresh JobLog ('refresh-scenario-splits'). Runs after the coordinator's
  night-of appends by construction (games final well before 05:10 ET).
- `flask refresh-splits [--sport nba]` manual CLI (same core function).
- `flask show-splits --player NAME --stat pts [--dim DIM]` inspection CLI:
  table of buckets, n, raw vs shrunk means vs baseline.

## Testing

Synthetic mini-store fixtures with hand-computed expectations:
- every bucket function (incl. leakage-safety of opp_def_tier — a game must
  not see same-day or later games in its opponent tier),
- shrinkage math vs hand-computed values incl. k fitting on a toy league,
- odds import: abbr normalization, idempotency, join-rate report, playoff
  flagging, score cross-validation report,
- --update-stats: merges missing keys, never overwrites, insert path
  unchanged,
- engine end-to-end into ScenarioSplit (counts, unique constraint, both
  season scopes, n≥3 filter, NULL-bucket exclusion for missing odds),
- refresh guard (no-change skip; change triggers),
- agreement score directionality + weighting on a constructed case,
- CLIs invoke + output format.
Full suite + coverage ≥ 80% + ruff + bandit, foreground, per repo standard.

## Non-goals (Plan B)

- No FEATURE_KEYS/model/retraining changes (Plan C consumes the table).
- No UI (Phase 2's scenario explorer reads the same table).
- No line_move dimension, no referee data (deferred).
- No MLB/NFL (dimensions registry is sport-keyed for later, NBA-only now).
- No changes to PlayerGameLog or existing services beyond the two import
  paths' score enrichment.

## Rollout

Feature branch via subagent-driven development (per-task review + final
whole-branch review), merged to local main after full-suite verification.
Operational order after merge: (1) hoopR re-import with --update-stats,
(2) import-betting-lines, (3) flask refresh-splits, (4) spot-check via
show-splits against a known player (e.g. LeBron home/away pts).
