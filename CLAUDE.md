# Edge Tracker — Claude Context

## Project
Flask + SQLAlchemy NBA betting tracker (app name: **Edge Tracker**) with ML projections (XGBoost/scikit-learn), APScheduler background jobs, and Railway deployment.

## Running Tests
```
source .venv/bin/activate && SECRET_KEY=test python -m coverage run -m unittest discover -s tests -v
python -m coverage report --include="app/*"   # CI requires ≥ 80%
```
- Test runner is **unittest** (not pytest). `SECRET_KEY` env var is required or app raises.
- `.venv/` is the active virtualenv; `venv/` and `venv310/` are stale.

## Linting
```
source .venv/bin/activate && ruff check .
source .venv/bin/activate && bandit -q -r app -x tests -ll
```
- CI runs both on every push — run locally before committing to catch issues early
- **Gotcha**: auto-generated Alembic merge migrations (`flask db merge`) always include unused `from alembic import op` / `import sqlalchemy as sa` — delete both lines before committing or ruff will fail

## Key Conventions
- All dates/times use **ET** (`ZoneInfo("America/New_York")` / `"US/Eastern"`) — normalization is critical for daily freshness checks and snapshot reads/writes.
- `_is_non_server_invocation()` in `app/__init__.py` guards scheduler startup — never start APScheduler in pytest/alembic/CLI contexts.
- ML model JSON artifacts (`app/ml_models/*.json`) are gitignored — stored on S3 in prod, generated locally via CLI.

## Project Layout
- `app/routes/` — Flask blueprints (auth, bet, main)
- `app/services/` — business logic: `scheduler.py`, `nba_service.py`, `ml_model.py`, `pick_quality_model.py`, `postmortem_service.py`, `stats_service.py`, `projection_engine.py`, `value_detector.py`, `ml_feature_builder.py` (canonical shared feature builder — `FEATURE_KEYS` must stay in sync between training and inference), etc.
- `app/ml_models/` — model artifact files (gitignored JSON)
- `app/cli.py` — Flask CLI commands (`flask refresh-stats`, calibration reports, etc.)
- `tests/` — unittest test files; CI runs all of them

## Environment
- `SECRET_KEY` — required to start the app (raises `RuntimeError` if missing)
- `.env` at root — gitignored, contains DB URL, API keys, S3 config
- `.env.example` — reference for required vars

## Database
- **Neon** (serverless PostgreSQL) in production, connected via `psycopg2-binary`
- SQLite (`instance/app.db`) for local dev
- Migrations managed with Flask-Migrate (Alembic): `flask db upgrade`

## CI (GitHub Actions)
- Runs on push/PR to `main`
- Python 3.11, `pip install -r requirements.txt`
- `SECRET_KEY=ci-test-secret-key-not-for-production`
- 80% coverage gate: `python -m coverage report --include="app/*" --fail-under=80`

## Deployment
- **Railway** — sole deployment target (`railway.toml`, `gunicorn.conf.py`)
- **S3** (boto3) — ML model artifact storage
- Live URL: `https://sportsbettingtracker-production.up.railway.app`
- Project: `shimmering-youth` · Service: `sports_betting_tracker` · Environment: `production`

## Railway Logs Access
```
railway login --browserless   # auth if needed (non-interactive)
railway link                  # select: shimmering-youth → production
railway service sports_betting_tracker
railway logs
```
- Auto-deploys on every push to `main` (no manual deploy step needed)
- Health check endpoint: `/health` → `{"status": "healthy"}`

## Key Model Fields (avoid wrong-field errors)
- `Bet`: `outcome` (not `result`), `prop_line` (not `line`/`over_under`), `source='auto_generated'` for auto picks
- `BetPostmortem`: `projected_stat`, `actual_stat`, `stat_type`, `prop_line` — join to `Bet` for `prop_type`/`player_name`
- `PlayerGameLog`: `win_loss` ('W'/'L'), `plus_minus` (float), `team_abbr`, `home_away` ('home'/'away')

## Local DB Scripts (connect to Neon, not SQLite)
```
source .venv/bin/activate && export $(grep -v '^#' .env | grep -v '^\s*$' | xargs) 2>/dev/null; python << 'PYEOF'
# script here
PYEOF
```
- `set -a && source .env` fails on `.env` lines with `&` — use the `export $(xargs)` form instead

## Local Dev Startup
```
source .venv/bin/activate && export $(grep -v '^#' .env | grep -v '^\s*$' | xargs) 2>/dev/null; flask run
```

## Force Model Retrain
```
source .venv/bin/activate && export $(grep -v '^#' .env | grep -v '^\s*$' | xargs) 2>/dev/null; flask retrain --force
```
- Takes ~8-10 min; Neon drops SSL after ~5 min idle — `db.session.remove(); db.engine.dispose()` before DB writes prevents this
- `railway run flask retrain --force` will FAIL (same SSL timeout) — Railway's 10:30 AM scheduler is the correct retrain path in prod
- Guardrail: skips retrain if active model is < 7 days old OR no new PlayerGameLog rows since last train
- After local retrain, models are stored with LOCAL paths — reactivate the latest S3 entries in `ModelMetadata` for Railway to use

## ML Models Architecture
- Model 1 (projections): 6 XGBoost regressors per stat type — `FEATURE_KEYS` in `ml_feature_builder.py` must stay in sync between training and inference (currently 37 features)
- Model 2 (pick quality): XGBoost classifier — `.pkl` when calibrated, `.json` fallback; `MODEL2_TIME_AWARE_SPLIT=true` env var on Railway
- Models stored as S3 paths in `ModelMetadata.file_path`; local paths won't work on Railway
- Reactivate S3 models after local retrain: set `is_active=False` on local entries, `is_active=True` on latest S3 entries per model name

## Git Workflow
- Always `git pull --rebase origin main` before push — Railway CI pushes can cause divergence
- Exclude `instance.bak/` and `tests/helpers.py.backup` from commits (untracked noise, not gitignored)

## Definition of Done
- Ensure no horizontal overflow at `320px` viewport width on the bets list.
- Ensure no overlap between status / P&L / actions at breakpoints `1200`, `992`, `768`, `576`, and `375`.
- Ensure all live-progress rows show current stat, line, period, clock, game-state, projection, and trend.
- Validate over/under trend semantics with at least one concrete **over** example and one concrete **under** example.
- Verify existing controls remain unchanged: filters, search, export, add, check now, manual grading, parlay toggle, and delete.
- Update tests for endpoint payload and key render paths.
- If visual changes are substantial, include before/after screenshots for desktop and mobile widths.
