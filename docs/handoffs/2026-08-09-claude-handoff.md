# Claude handoff — 2026-08-09

This is the durable save point for the combined ML-validation/backtesting and
frontend design-system work on `phase-2-increment-a`.

## Repository state

- ML validation/backtesting implementation: `44e5b2a`.
- Owned frontend/design-system implementation: `0cd037f`.
- Claude's `worktree-frontend-overhaul` work is already represented in this
  branch by `cc62a9e`; do not cherry-pick that worktree again.
- The application remains local-first. Railway and production model storage
  are inactive.
- Apply migrations before exercising the new evaluation workflow:

  ```bash
  source .venv/bin/activate
  flask db upgrade heads
  ```

- A fresh SQLite database now traverses the complete migration chain to
  `7ca81e2b4f10`, downgrades to `21dc7a4c6b61`, and upgrades again. The old
  `b6f9fdecc99a` cascade migration needed a naming convention because SQLite
  does not preserve unnamed foreign-key constraint names.

## ML findings and implementation

The earlier review was correct: the old `flask backtest` established
probability calibration on synthetic lines, not betting profitability. It is
now labeled as a synthetic-line calibration smoke test.

The five follow-ups are implemented as infrastructure, with these boundaries:

1. **Underfitting diagnostics:** point and distributional training persist
   train/validation MAE, normalized error, baseline improvement,
   generalization gap, and under/overfit flags. `flask model-diagnostics`
   adds residual slices and optional temporal learning curves. Runs are stored
   in `ModelEvaluationRun` with configuration and Git revision.
2. **Model 2 temporal isolation:** Model 2 has no random-split path. It
   requires 400 clean dated picks and separates fit, early stopping,
   calibration, and untouched test partitions at date boundaries. It refuses
   activation if partitions lack minimum size or both outcomes.
3. **Real player-prop quotes:** `OddsSnapshot` stores source/event/player
   identity, event time, bookmaker-specific line and prices, snapshot kind,
   and an idempotency key. The scheduler captures T-60 decision and T-10 close
   windows; `flask import-player-prop-odds` ingests licensed CSV/JSON history.
4. **Rolling-origin evaluation:** `flask rolling-backtest` uses expanding
   monthly folds and temporary non-active models. Current-only defensive
   features are neutralized when point-in-time values are unavailable.
5. **Economic metrics and gates:** reports contain ECE/Brier/log loss,
   flat-unit ROI, game-clustered confidence intervals, line and no-vig price
   CLV, drawdown, quarter-Kelly results, and edge-threshold segments.
   Promotion stays `SHADOW` below coverage gates, then requires two consecutive
   qualifying runs (`PROMOTE_CANDIDATE` then `PROMOTE`). Nothing auto-activates.

### ML limitations that remain

- No large historical real-line player-prop dataset has been imported yet, so
  there is still no evidence of positive ROI or CLV. Live decision/close rows
  must accumulate or licensed history must be imported.
- Historical injury segmentation is unavailable until point-in-time injury
  records exist.
- Model 2 remains inactive until the database contains enough clean, resolved,
  dated picks across all four temporal partitions.
- The rolling backtest is intentionally shadow-only until its join, fold, and
  closing-line coverage gates are met.

Operational details and commands are in `docs/runbooks/ml-validation.md` and
`docs/runbooks/retrain.md`.

## Frontend/design-system findings and implementation

- Bootstrap CSS/JS, Bootstrap Icons, Google Fonts, and `data-bs-*` attributes
  are removed. The app owns its framework layer, local fonts, inline SVG icon
  macro, dropdown/collapse/modal/toast behavior, and responsive grid.
- The 992px shell/builder overflow, narrow dashboard KPI cards, mobile stat
  filters, low-contrast empty state, heading semantics, touch targets, and
  dynamic Playwright account collisions are fixed.
- Playwright uses port 5010 and the repository virtualenv. Visual baselines
  cover the six core pages on desktop and mobile; responsive tests cover
  1200/992/768/576/375/320px; Axe gates serious and critical findings.

### Frontend debt that remains

- `app/static/css/theme.css` is a monolith. A future refactor should split it
  into tokens, primitives, components, utilities, and page-specific layers
  without changing selectors in the same commit.
- The owned framework still uses Bootstrap-shaped compatibility names such as
  `.row`, `.btn`, and spacing utilities.
- Current visual baselines emphasize empty states. Add deterministic populated
  bets, live-game, modal, toast, and validation-error fixtures next.

## Verification at this save point

- `1210` Python unit tests pass.
- App coverage is `80%` (`10970` statements, `2244` missed).
- `22` Playwright tests pass, including visual, responsive, and Axe coverage.
- `ruff check .`, `bandit -q -r app -x tests -ll`, JavaScript syntax,
  UI-class manifest, and `git diff --check` pass.
- Fresh SQLite full-chain upgrade, new-migration downgrade, and re-upgrade pass.

## Recommended Claude resume order

1. Read this file and `docs/runbooks/ml-validation.md`.
2. Confirm `git status --short` is clean and pull this branch.
3. Do not claim profitability until real decision/close quote coverage exists.
4. If continuing ML work, prioritize importing licensed prop history and run
   the rolling evaluator in shadow mode.
5. If continuing UI work, prioritize deterministic populated-state visual
   fixtures before reorganizing the CSS monolith.
