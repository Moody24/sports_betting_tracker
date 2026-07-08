# Edge Tracker Platform Upgrade — Phase 1: Distributional ML + Scenario Engine

**Date:** 2026-07-07
**Status:** Approved design, pending implementation plan

## Roadmap Context

This is Phase 1 of a four-phase upgrade. Later phases get their own specs.

| Phase | Scope |
|---|---|
| **1 (this spec)** | Data platform, scenario engine, distributional model, calibration, CLV, Kelly staking — backend + CLI |
| 2 | UI rebuild: drop Bootstrap, owned component library on Obsidian Terminal tokens, distribution-curve pick cards, model health dashboard, scenario explorer |
| 3 | MLB support (service, ingestion, props) on the multi-sport foundations laid here |
| 4 | NFL support |

## Goals

- Replace point-estimate projections with **full predicted distributions** per stat, so P(over) is computable for any line.
- Build a **scenario engine**: conditional performance splits across many dimensions, statistically shrunk, feeding both model features and (in Phase 2) the UI.
- Put value detection on a probabilistic footing: calibrated P(over) vs de-vigged market probability, **CLV tracking**, and **fractional Kelly** stake sizing.
- Fix the data foundation: permanent sport-aware historical game logs (NBA now; MLB/NFL schemas ready), advanced box scores, and production-grade ingestion (API budget, tiered polling, event-driven triggers).

## Non-Goals (Phase 1)

- No new UI pages (Phase 2). Existing pages keep working via the median projection.
- No MLB/NFL ingestion or services (Phases 3–4) — only schema readiness.
- No deep learning. The model family stays XGBoost (existing dependency).
- "Extremely accurate" is not a target; **beating the closing line consistently** (positive CLV, calibrated probabilities) is the success metric.

## Architecture Overview

```
Ingestion (nba_api / ESPN / The Odds API)
   │  APIBudgetManager (quota headers, tiered polling, no re-fetch)
   ▼
HistoricalGameLog (permanent, sport-aware)  +  PlayerGameLog (slate cache, unchanged)
   │
   ├─► ScenarioEngine ──► ScenarioSplit (materialized nightly)
   │                          │ (split features, agreement score)
   ▼                          ▼
ml_feature_builder (canonical, extended FEATURE_KEYS)
   ▼
Model 1': multi-quantile XGBoost per stat ──► predicted distribution
   ▼                                              │
IsotonicCalibrator ──► calibrated P(over line)    │ copula for combos
   ▼                                              ▼
ValueDetector': edge = P(over) − de-vigged implied prob
   ▼
Auto-picks (probability thresholds) + Kelly stake + CLV capture
```

## Components

### 1. Data platform

**`HistoricalGameLog` (new table)** — permanent training store, separate from the
`PlayerGameLog` slate cache (which keeps its pruning behavior untouched).

- Common columns: `sport` (indexed, `'nba' | 'mlb' | 'nfl'`), `player_id`, `player_name`,
  `team_abbr`, `opp_abbr`, `game_id`, `game_date`, `home_away`, `win_loss`, `starter` (bool),
  `season` (e.g. `'2025-26'`).
- `stats` JSON column holds the per-sport stat payload (NBA: pts/reb/ast/…, plus advanced:
  usage_pct, minutes, plus_minus, on/off where available; MLB: hits/TB/Ks/…; NFL:
  pass_yds/rush_yds/receptions/…). Per-sport stat catalogs are defined in a
  `SPORT_STAT_CONFIG` registry next to `SPORT_REGISTRY` so the feature builder is
  sport-parameterized from day one.
- Unique constraint `(sport, player_id, game_id)`; indexes on `(sport, player_name, game_date)`.
- Rationale for JSON payload: three sports with disjoint stat sets; training reads are
  bulk scans into pandas, not per-column SQL filters, so JSON costs little and avoids
  three near-identical tables or a 60-column sparse table. Hot common fields stay as
  real columns.

**Backfill CLI** — `flask backfill-logs --sport nba --seasons 3 [--resume]`.
Pulls season game logs via `nba_api`, including advanced box scores (usage, starter flag).
Idempotent (upsert on the unique key), resumable, rate-limited, logs progress to `JobLog`.

**`APIBudgetManager` (new service)** — wraps outbound calls to The Odds API and stats APIs:
- Records The Odds API `x-requests-remaining` / `x-requests-used` headers; exposes budget
  status; refuses non-critical calls under a configurable floor (`ODDS_API_BUDGET_FLOOR`).
- Tiered polling: scheduler jobs consult game windows — tight cadence only while games are
  live, sparse otherwise. Providers offer no webhooks; this is the production substitute.
- Event-driven internal triggers: game-final detection fires grade → postmortem → split
  refresh as a chain instead of independent timers discovering state.
- Never re-fetches data already persisted (checks `HistoricalGameLog` before boxscore calls).

### 2. Scenario engine

**`app/services/scenario_engine.py`** — computes conditional splits per (player, stat) over
`HistoricalGameLog`.

Dimensions (each a small enum of buckets):
`home_away`, `rest_bucket` (0/1/2/3+ days), `opp_def_tier` (top10/mid/bottom10),
`pace_tier`, `fav_dog`, `total_bucket` (low/mid/high O/U), `season_segment`,
`game_script` (close/blowout), `role` (starter/bench), `teammate_context`
(key teammate in/out, from lineup data where available), `line_move`
(spread moved toward/away). Referee crew is explicitly **best-effort/deferred** —
reliable free data is not available.

- Splits are computed for single dimensions and **pairwise combinations** (not the full
  power set — beyond 2-way, samples are pure noise). Each split stores `n`, raw mean,
  and a **shrunk mean** via empirical Bayes: `shrunk = (n·raw + k·baseline) / (n + k)`
  with per-stat prior strength `k` fit from league variance. This is what makes
  "all scenario combos" honest instead of noise-chasing.
- **Materialized nightly** into a new `ScenarioSplit` table by a scheduler job
  (registered job #18), so reads are instant for both feature building and Phase 2 UI.
- Outputs for the model: the shrunk split values matching tonight's context, plus a
  **split agreement score** (weighted share of applicable splits pointing the same
  direction relative to the line).

### 3. Distributional model

**Model 1 rebuild** — per stat type, one XGBoost model with
`objective="reg:quantileerror"` and `quantile_alpha=[0.05, 0.1, …, 0.95]`
(multi-quantile in a single booster; XGBoost ≥ 2.0).

- `ml_feature_builder.py` remains the **single canonical feature source**;
  `FEATURE_KEYS` is extended with the scenario features. Sport-parameterized via
  `SPORT_STAT_CONFIG` (NBA only exercised in Phase 1).
- Predicted quantiles → monotonic-rectified empirical CDF → `prob_over(line)` and
  `prob_under(line)` for any line; the 0.50 quantile is the point projection, keeping
  `projection_engine` and `value_detector` consumers working during migration.
- Existing bias-correction constants (`COMBO_PROP_BIAS_CORRECTION` etc.) are retired for
  distribution-based markets; the calibration layer replaces them.
- **Combo props / SGP:** a Gaussian copula over per-stat marginal CDFs with correlation
  matrices estimated per player archetype from historical logs → joint P(over) for
  PRA and multi-leg combinations instead of naive independence.
- **Model 2 (pick quality)** gains features: predicted distribution width (q90−q10),
  split agreement score, line distance from median in distribution units.
- Training: date-based walk-forward split (unchanged policy), early stopping,
  artifacts under `MODEL_STORAGE` as today. Retrain guardrails unchanged.

**Calibration layer** — isotonic regression fit on walk-forward out-of-fold
`(predicted P(over), actual over)` pairs, serialized with model artifacts, applied at
inference. Reliability curves (expected vs observed frequency in probability bins) are
computed at every retrain and stored in `JobLog` details for the Phase 2 dashboard.

### 4. Value detection, CLV, staking

- **De-vig:** implied probabilities from over/under odds normalized to remove the book's
  margin (multiplicative method).
- **Edge:** `calibrated P(over) − de-vigged implied P(over)`. Auto-pick thresholds move to
  probability space: `AUTO_PICK_MIN_PROB_EDGE_*` env vars (straight/2-leg/3-leg), with the
  existing point-based thresholds honored as fallback until the new model is active.
- **CLV capture:** a scheduler step snapshots the final pre-tip line/odds for every pending
  pick (new columns on `Bet`: `closing_line`, `closing_odds`, `clv`); postmortems report
  CLV alongside win/loss. CLV is the primary model-quality KPI.
- **Kelly staking:** with `p` = calibrated win probability and `b` = decimal odds − 1,
  full Kelly is `f* = (p·(b+1) − 1) / b`; recommended stake =
  `bankroll × KELLY_FRACTION × max(f*, 0)` (`KELLY_FRACTION` default 0.25, `BANKROLL`
  env-configured). Stored on the pick as a recommendation only — no auto-wagering
  behavior changes.

### 5. CLI & scheduler

- `flask backfill-logs` (above).
- `flask backtest [--from DATE --to DATE]` — walk-forward backtest reporting: calibration
  error (ECE), Brier score, hit rate by probability bucket, ROI at flat and Kelly stakes,
  and CLV where closing lines exist. Run before/after every model change.
- Scheduler additions: nightly `ScenarioSplit` refresh, closing-line snapshot job,
  event-chain trigger on game-final. All respect `_is_non_server_invocation()`.

## Error Handling

- Backfill: per-player try/except with error accumulation in `JobLog`; partial progress
  persists (upserts); `--resume` skips completed (player, season) pairs.
- Missing scenario data (e.g. no lineup info): features fall back to the player baseline
  with `n=0` recorded — never fabricated splits.
- Odds budget exhausted: critical jobs (bet grading) proceed via stats APIs; discretionary
  jobs (prop scans) skip with a logged reason.
- Model artifacts missing quantile heads (stale artifact): inference falls back to the
  legacy point model if present, else surfaces "model unavailable" as today.

## Testing

- unittest throughout (no pytest), `SECRET_KEY=test`, 80% coverage gate, ruff + bandit.
- Unit: shrinkage math, CDF construction/monotonicity, de-vig, Kelly, copula correlation
  bounds, budget manager header parsing, backfill idempotency (mocked API).
- Integration: feature builder train/inference parity on synthetic `HistoricalGameLog`
  rows (extending the existing parity-test pattern); backtest command on a seeded DB.
- Regression: existing projection consumers keep working off the median quantile.

## Migration & Rollout

1. Migrations: `HistoricalGameLog`, `ScenarioSplit`, `Bet` closing-line columns.
2. Backfill 3 NBA seasons locally; verify row counts and spot-check against nba.com.
3. Train new model alongside old (separate artifact names); run `flask backtest` comparing
   both; new model activates only if calibration and CLV-proxy metrics beat the old one.
4. Flip value detection to probability edges; monitor via postmortems.

## Success Criteria

- Backtest shows calibration ECE ≤ 0.03 and reliability curve near-diagonal.
- Positive mean CLV on tracked picks over a rolling 100-pick window.
- Retrain completes locally in < 30 min with 3 seasons of data.
- All existing tests green; coverage gate holds; no scheduler regressions.
