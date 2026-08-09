# ML Validation and Real-Line Backtesting

Apply migrations before using the evaluation commands:

```bash
source .venv/bin/activate
flask --app run.py db upgrade heads
```

## Underfitting diagnostics

```bash
flask model-diagnostics --stat-type player_points
flask model-diagnostics --stat-type player_points --learning-curves
```

The command records its configuration, Git revision, train/validation errors,
baseline comparison, and residual slices in `ModelEvaluationRun`. Historical
injury segmentation is reported as unavailable until point-in-time injury data
exists.

Model 2 no longer has a random-split option. It requires at least 400 clean,
dated resolved picks and isolates fit, early-stopping, calibration, and test
partitions before replacing an active artifact.

## Player-prop quote history

Live jobs keep the existing scheduled snapshots and also capture decision
quotes around T-60 and closing quotes around T-10. Each bookmaker's own line
and prices are retained.

Licensed historical data can be imported with:

```bash
flask import-player-prop-odds \
  --file /path/to/player_props.csv \
  --format csv \
  --source provider_name
```

CSV and JSON rows require:

`source_event_id`, `event_start_time`, `snapped_at`, `player_name`, `market`,
`bookmaker`, `line`, `over_odds`, and `under_odds`.

Naive timestamps are interpreted as ET; offset-aware timestamps are converted
to UTC. Imports are idempotent and post-tip observations are rejected.

## Rolling real-line backtest

```bash
flask rolling-backtest \
  --stat-type player_points \
  --date-from 2026-10-01 \
  --date-to 2027-04-30 \
  --edge-threshold 0.03
```

The evaluator uses expanding monthly folds and never activates its temporary
models. Reports include calibration, flat-unit ROI, game-clustered confidence
intervals, directional line CLV, same-line no-vig price CLV, maximum drawdown,
and quarter-Kelly results across all configured edge thresholds.

Economic promotion remains `SHADOW` until the stored coverage requirements are
met. A qualifying result first becomes `PROMOTE_CANDIDATE`; only a second
consecutive qualifying run becomes `PROMOTE`, and neither verdict changes the
active model automatically.

`flask backtest` remains available as a synthetic-line calibration smoke test.
It must not be used as evidence of ROI or CLV.
