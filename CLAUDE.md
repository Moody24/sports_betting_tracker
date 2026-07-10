# Edge Tracker — Claude Context

## Project
Flask + SQLAlchemy NBA betting tracker with XGBoost ML projections and APScheduler background jobs. Running locally; Railway deployment inactive.

## Local Dev Startup
```bash
source .venv/bin/activate && export $(grep -v '^#' .env | grep -v '^\s*$' | xargs) 2>/dev/null; flask run
```
- `SECRET_KEY` is required — app raises `RuntimeError` if missing
- `.venv/` is the active virtualenv; `venv/` and `venv310/` are stale

## Running Tests
```bash
source .venv/bin/activate && SECRET_KEY=test python -m coverage run -m unittest discover -s tests -v
python -m coverage report --include="app/*"
```
- Test runner is **unittest** (not pytest)

## Linting
```bash
source .venv/bin/activate && ruff check .
source .venv/bin/activate && bandit -q -r app -x tests -ll
```
- Run both before every commit — CI enforces them on push

## Key Conventions
- All dates/times use **ET** (`ZoneInfo("America/New_York")`) — critical for freshness checks and snapshot reads/writes
- `_is_non_server_invocation()` in `app/__init__.py` guards scheduler startup — never start APScheduler in pytest/alembic/CLI contexts
- Scheduler has 20 registered jobs as of 2026-07-10 (game_day_coordinator + hoopr_reconcile added in Plan A2)
- ML model artifacts (`app/ml_models/*.json`) are gitignored — regenerate with `flask retrain --force`

## Skills (load on demand)
- **`ml-models`** — XGBoost architecture, calibration constants, auto-pick thresholds, retrain, odds API
- **`database`** — DB config, migrations, key model fields (Bet/BetPostmortem/PlayerGameLog), local DB scripts
- **`deployment-ci`** — GitHub Actions CI, Railway deployment, git workflow, gunicorn
- **`project-layout`** — Flask blueprints, services map, multi-sport expansion
- **`definition-of-done`** — UI breakpoints, live-progress rows, control regression checklist
