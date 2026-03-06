# Sports Betting Tracker — Claude Context

## Project
Flask + SQLAlchemy NBA betting tracker with ML projections (XGBoost/scikit-learn), APScheduler background jobs, and Railway deployment.

## Running Tests
```
source .venv/bin/activate && SECRET_KEY=test python -m coverage run -m unittest discover -s tests -v
python -m coverage report --include="app/*"   # CI requires ≥ 80%
```
- Test runner is **unittest** (not pytest). `SECRET_KEY` env var is required or app raises.
- `.venv/` is the active virtualenv; `venv/` and `venv310/` are stale.

## Key Conventions
- All dates/times use **ET** (`ZoneInfo("America/New_York")` / `"US/Eastern"`) — normalization is critical for daily freshness checks and snapshot reads/writes.
- `_is_non_server_invocation()` in `app/__init__.py` guards scheduler startup — never start APScheduler in pytest/alembic/CLI contexts.
- ML model JSON artifacts (`app/ml_models/*.json`) are gitignored — stored on S3 in prod, generated locally via CLI.

## Project Layout
- `app/routes/` — Flask blueprints (auth, bet, main)
- `app/services/` — business logic: `scheduler.py`, `nba_service.py`, `ml_model.py`, `pick_quality_model.py`, etc.
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

## Definition of Done
- Ensure no horizontal overflow at `320px` viewport width on the bets list.
- Ensure no overlap between status / P&L / actions at breakpoints `1200`, `992`, `768`, `576`, and `375`.
- Ensure all live-progress rows show current stat, line, period, clock, game-state, projection, and trend.
- Validate over/under trend semantics with at least one concrete **over** example and one concrete **under** example.
- Verify existing controls remain unchanged: filters, search, export, add, check now, manual grading, parlay toggle, and delete.
- Update tests for endpoint payload and key render paths.
- If visual changes are substantial, include before/after screenshots for desktop and mobile widths.
