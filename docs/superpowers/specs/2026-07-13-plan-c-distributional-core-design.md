# Plan C — Increment 1: Distributional Core (Design)

**Date:** 2026-07-13
**Status:** Implemented (Increment 1) — see docs/superpowers/plans/2026-07-13-plan-c-distributional-core.md
**Roadmap:** Plan C of the ML platform upgrade (Phase 1 spec: `docs/superpowers/specs/2026-07-07-ml-platform-upgrade-phase1-design.md`). Follows Plans A, A2, B (all complete).

## Goal

Replace today's *synthetic* over/under probability — a `Normal(projection, std_dev)` CDF around a point estimate (`ValueDetector._model_prob_over`, `app/services/value_detector.py:271`) — with a **real, calibrated predictive distribution** per (player, stat) that yields `P(over line)` for any line. Keep the point projection (distribution median / count mean) so every existing route, service, and template keeps working unchanged during migration.

This is **Increment 1** of Plan C: the distributional core and its calibration. It is deliberately the smallest safe slice.

## Non-Goals (explicitly deferred)

- **Scenario-engine features / `agreement_score` wiring.** Blocked by the ESPN↔NBA player-id crosswalk: `ScenarioSplit.player_id` is an ESPN athlete id, live scoring has only player names / NBA-stats ids, and no crosswalk exists (`app/services/espn_mapping.py` has team-abbr normalization only). Resolving that is its own future increment.
- **Copula / SGP joint combos.** PRA is handled directly (below); multi-leg joint probability via Gaussian copula is a later increment.
- **CLV capture and Kelly staking** (`Bet.closing_line/clv`, fractional Kelly). Later increment.
- **Live-context builder** for the 10 scenario dimensions (`build_context` is historical/post-hoc only). Later increment.
- **All UI / Phase 2** (reliability dashboard, distribution display). This increment changes no templates.

## Architecture

### Model heads (hybrid, per stat)

The current model is one XGBoost point-regressor per stat (`app/services/ml_model.py`), with per-stat objectives in `STAT_TRAINING_CONFIG` (`ml_model.py:38`). Increment 1 adds distributional heads as **new artifacts under separate names**, leaving the point path intact.

- **Continuous stats — points, rebounds, assists:** new **multi-quantile XGBoost** — a single booster per stat, `objective="reg:quantileerror"`, `quantile_alpha=[0.05, 0.15, …, 0.85, 0.95]` (XGBoost 2.1.3 confirmed to support multi-quantile single-booster). Predicted quantiles are **monotone-rectified** (elementwise cumulative-max across the sorted alpha grid) to guarantee a valid non-decreasing quantile function. `P(over line) = 1 − CDF(line)`, where CDF(line) is obtained by interpolating line against the rectified (quantile-value → alpha) map, clamped to [0, 1]. **The q0.50 quantile is the point projection.**
- **PRA (points+rebounds+assists):** a multi-quantile model trained **directly on realized PRA**, not summed from components. This retires the `+3.2` `COMBO_PROP_BIAS_CORRECTION` (`projection_engine.py`) on the distributional path (that constant existed to patch summed-independence bias; a direct model learns the joint).
- **Count stats — threes, steals, blocks:** **reuse the existing `count:poisson` regressors** (these stats already train with a Poisson objective). Interpret the predicted mean λ as a **Poisson(λ)** distribution: `P(over line) = 1 − PoissonCDF(⌊line⌋, λ)` (props use half-integer lines, so no tie ambiguity). Mean = point projection. Negative-binomial (overdispersion) is a **noted future refinement** — Poisson is native to XGBoost; NB would need a custom objective.

### Features

Reuse the existing 30-key `FEATURE_KEYS` and the canonical shared builder `app/services/ml_feature_builder.py` **unchanged**. No scenario features are added this increment (they are blocked and deferred). The distributional heads consume exactly the features the point model consumes today, preserving train/inference parity.

### Calibration

The raw model `P(over)` is calibrated with a **fresh walk-forward out-of-fold isotonic** step (the Phase 1 spec's approach, and the statistically correct one for a per-line probability):

- Across a temporal walk-forward split, collect out-of-fold `(predicted P(over), realized over ∈ {0,1})` pairs, pooled across lines and players (per-stat calibrators).
- Fit `sklearn` isotonic regression on those pairs; serialize the calibrator **with the model artifact**; apply at inference to every `P(over)` before it reaches the staking path.
- **Reuse** the existing reliability tooling in `pick_quality_model.get_calibration_report` (`app/services/pick_quality_model.py:613`) — reliability curve, Brier, log-loss, ECE — for evaluation and to log reliability to `JobLog` each retrain (a later Phase 2 dashboard reads it).

The additive bias-correction constants (`SINGLE_STAT_BIAS_CORRECTION`, `COMBO_PROP_BIAS_CORRECTION`) are **retired on the distributional path** — calibration subsumes them.

## Integration

- New model artifacts are stored under distinct names (e.g. `dist_<stat>`) via the existing `model_storage` layer, so the incumbent point model is never overwritten.
- `ValueDetector._model_prob_over` gains a branch: when the distributional model is active, `P(over)` comes from the calibrated model CDF instead of `Normal(projection, std_dev)`. The **point projection** surfaced to `ProjectionEngine` / routes / templates is the distribution median (continuous) or Poisson mean (counts) — display is unchanged.
- Gated by a new env flag **`USE_DISTRIBUTIONAL_MODEL`** (default `false`), mirroring the existing `USE_ML_PROJECTIONS` pattern (`projection_engine.py:436`). Off = today's behavior exactly.

## Training, Evaluation, Rollout (safety)

- **Shadow training:** the distributional heads train **alongside** the incumbent point models under separate artifact names; the incumbent stays the default.
- **Backtest gate:** `flask backtest` (extended) runs a walk-forward comparison of the calibrated distributional `P(over)` (reliability / ECE) plus a CLV-proxy against the incumbent's synthetic-Gaussian `P(over)`. The new model is promoted to active **only if it beats the incumbent** on calibration.
- **Success gates** (from the Phase 1 spec): out-of-fold ECE ≤ 0.03; full retrain < 30 minutes.
- The `retrain` CLI is extended to build the distributional heads + calibrators; artifacts persist through the existing storage path.

## Testing

`unittest`, no network, no real `instance/app.db`; 80% coverage gate; `ruff` + `bandit` clean.

- **Unit:** quantile monotone-rectification; CDF interpolation and clamping; `P(over)` monotone-decreasing in the line and ∈ [0, 1]; Poisson analytic `P(over)` matches `scipy.stats.poisson`; isotonic calibrator fit + apply; feature parity assertion (reuses the existing builder's parity check).
- **Behavioral / integration:** median == point projection surfaced to `ProjectionEngine`; `USE_DISTRIBUTIONAL_MODEL=false` reproduces current behavior exactly; with the flag on, `ValueDetector` P(over) comes from the calibrated model; calibrated reliability (ECE) beats the synthetic Gaussian on a held-out fixture.
- **Determinism:** fixed seeds; small synthetic training fixtures so tests stay fast and offline.

## Key Design Decisions (rationale)

1. **Hybrid heads** — quantile for pts/reb/ast/PRA (honest empirical CDF where prop volume is), Poisson-CDF for the count stats (a 9-quantile CDF is coarse on 0–5 integer stats; the Poisson regressors already exist). Minimal new training risk.
2. **PRA direct, not summed** — learns the joint, retires the independence-bias hack; copula deferred.
3. **Reuse features + calibration reporting** — no train/inference skew, no duplicate reliability tooling.
4. **Env-gated shadow rollout** — the incumbent stays default until a backtest proves the new model is better-calibrated; zero risk to the running product.

## Risks / Open Items

- **Count-stat overdispersion:** if reliability curves show the Poisson heads are underdispersed, NB heads (custom objective) become a follow-up — surfaced by the reliability report, not silently.
- **Backtest data volume:** walk-forward OOF calibration needs enough resolved history; the 79,603-row store (3 seasons) should suffice, to be confirmed during implementation.
- **`USE_ML_PROJECTIONS` baseline:** it currently defaults `false`, so the incumbent P(over) in the running config is the heuristic engine's Gaussian, not the ML point model's — the backtest must compare against the *actual* running baseline, not a dormant one.

## What This Unblocks

A calibrated `P(over)` per (player, stat, line) is the foundation the later Plan C increments build on: scenario/agreement features (once the id crosswalk lands), copula combos, and CLV/Kelly staking all consume calibrated probabilities.
