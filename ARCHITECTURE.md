# Edge Tracker Architecture

The executable source of truth for dependencies, shared contracts, state,
scheduler ownership, and business time is
[`docs/architecture/system-contract.md`](docs/architecture/system-contract.md).
This file is the shorter onboarding map.

## System shape

```text
Browser
  -> Flask/Jinja routes
       -> application services and repositories
            -> SQLAlchemy models / provider adapters / ML artifacts
                 -> SQLite (local) or PostgreSQL (hosted)
                 -> ESPN and The Odds API

Dedicated scheduler process
  -> the same service layer
       -> database, providers, and local model artifacts
```

Edge Tracker is NBA-focused. It records private betting positions, grades
results, displays live NBA context, and produces analytical player-prop
projections. MLB and NFL are not implemented and must not reuse NBA models or
data under different labels.

## Application boundaries

### HTTP layer — `app/routes/`

- `auth.py`: registration, login, and logout.
- `main.py`: public pages, crawler documents, dashboard, and UX telemetry.
- `bet.py`, `bet_crud.py`, `bet_import.py`: position views, user-scoped writes,
  grading actions, and receipt/import workflows.
- `nba_analysis.py`, `nba_live.py`: player-prop/stat analysis and the current NBA
  slate.

Routes validate transport input, authorize the current user, and delegate domain
work. `app/route_policy.py` is the explicit method/auth/CSRF/response catalog and
is checked against Flask's URL map in tests.

### Service layer — `app/services/`

- Provider boundary: `espn_client.py`, `nba_service.py`, `api_budget.py`,
  `player_crosswalk.py`, and `espn_mapping.py`.
- Position lifecycle: `bet_placement_service.py`, `bet_context_service.py`,
  `postmortem_service.py`, `score_cache.py`, and `game_day_coordinator.py`.
- Analysis: `stats_service.py`, `matchup_service.py`, `analysis_context.py`,
  `context_service.py`, `live_context.py`, and scenario modules.
- ML: `ml_feature_builder.py`, `ml_model.py`, `pick_quality_model.py`,
  `distributional_model.py`, `distributional_predictor.py`,
  `projection_engine.py`, `rolling_backtest.py`, and calibration/diagnostic
  modules.
- Automation: `scheduler.py` owns one registry of 22 jobs and must run in exactly
  one scheduler process.

Routes must not import private helpers from other route modules. Service modules
must not depend on routes. Architecture tests enforce both directions.

### Command layer — `app/cli/`

Commands cover model training/evaluation, stats/history ingestion, market
governance, odds imports, scenario generation, observability, data quality, E2E
fixtures, and database cutover. Commands delegate to the same services used by
HTTP and scheduled jobs.

The database cutover entry point is:

```bash
flask migrate-sqlite-to-postgres --source instance/app.db \
  --target "$DATABASE_URL" --dry-run
```

It requires an upgraded, empty PostgreSQL target and validates the copy before a
cutover is accepted.

## Persistence

`app/models.py` currently defines 16 SQLAlchemy tables:

| Domain | Tables |
|---|---|
| Accounts and positions | `user`, `bet`, `bet_postmortem`, `pick_context` |
| Current/live data | `game_snapshot`, `player_game_log`, `team_defense_snapshot`, `injury_report`, `odds_snapshots` |
| Permanent training/context data | `historical_game_log`, `historical_game_odds`, `scenario_split`, `scenario_context_pack` |
| Models and operations | `model_metadata`, `model_evaluation_run`, `job_log` |

Alembic migrations in `migrations/versions/` are the schema history. CI replays
the full chain on SQLite and PostgreSQL 16. Application startup does not race
migrations; hosted deployment uses a blocking pre-deploy migration.

Business slate/date decisions use Eastern Time. Persisted event timestamps use
timezone-aware UTC where the model supports them. The precise rules are in the
system contract.

## ML contracts

Model 1 uses six XGBoost regressors for points, rebounds, assists, threes, steals,
and blocks. Training and inference share the ordered 30-feature contract in
`ml_feature_builder.py`.

Model 2 is a separate pick-quality classifier with mandatory temporal
fit/early-stop/calibration/test partitions. Distributional heads produce
calibrated probability estimates where enabled. Model activation is guarded by
metadata, feature hashes, rollback artifacts, real-line coverage, rolling-origin
evaluation, and economic/calibration gates.

The repository contains the engineering needed to evaluate models; it does not
contain enough licensed real decision/closing-line evidence to claim proven
profitability. See `docs/runbooks/ml-validation.md`.

## Runtime ownership

- Local development: SQLite, `SCHEDULER_ENABLED=false`, manual CLI jobs.
- Hosted safe baseline: one Gunicorn web worker with scheduler disabled, plus at
  most one dedicated scheduler process.
- Process-local rate limiting is accepted only for one web worker. Multiple web
  workers require a shared limiter store such as Redis or startup fails.
- PostgreSQL is the hosted durable store. Caches are derived and may never become
  the authority for settlement, ownership, or model promotion.

Health probes:

- `/health`: process liveness.
- `/ready`: bounded readiness checks for required dependencies.

## Local development

```bash
source .venv/bin/activate
export SECRET_KEY=dev-only-change-me
export DATABASE_URL=sqlite:///app.db
export SCHEDULER_ENABLED=false
flask --app run.py db upgrade heads
flask run
```

Live odds need `ODDS_API_KEY`; the application degrades explicitly when it is
absent or a provider is unavailable.

## Verification

```bash
.venv/bin/ruff check .
.venv/bin/bandit -q -r app -x tests -ll
SECRET_KEY=test .venv/bin/python -m coverage run -m unittest discover -s tests -v
.venv/bin/python -m coverage report --include="app/*"
./scripts/predeploy_guardrails.sh
npx playwright test
```

The test runner is `unittest`. Browser tests include functional, accessibility,
responsive, logout-isolation, and visual-regression contracts.

## Operational references

- Deployment/cutover (inactive): `docs/deploy.md`
- Database maintenance: `docs/runbooks/db-maintenance.md`
- ML retraining: `docs/runbooks/retrain.md`
- ML validation: `docs/runbooks/ml-validation.md`
- Incident response: `docs/runbooks/incident-response.md`
- Current launch boundary: `docs/launch-readiness-and-expansion-todo.md`
- Current retained debt: `docs/tech-debt-register.md`
- UI release contract: `docs/ui_v1_baseline.md`
