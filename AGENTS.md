# Edge Tracker — Codex Context

## Project
Flask + SQLAlchemy NBA betting tracker with XGBoost ML projections and APScheduler background jobs. Running locally; Railway deployment inactive.

Keep this repository focused on the tracker application and its engineering documentation. Store personal research, picks, chat transcripts, and live-result snapshots in the shared brain or external task artifact folders.

## Local Dev Startup
```bash
scripts/local_postgres.sh start
.venv/bin/python scripts/run_local.py
```
- `SECRET_KEY` is required — app raises `RuntimeError` if missing
- `.env` now points to the project-scoped local PostgreSQL 16 database; `instance/app.db` is retained as a SQLite rollback source
- Pass `--with-scheduler` to `scripts/run_local.py` when live jobs should run; the default local server leaves them disabled
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
- Architecture SSOT: `docs/architecture/system-contract.md` — read before changing modules, schemas, APIs, caches, jobs, or cross-module state
- All dates/times use **ET** (`ZoneInfo("America/New_York")`) — critical for freshness checks and snapshot reads/writes
- `_is_non_server_invocation()` in `app/__init__.py` guards scheduler startup — never start APScheduler in pytest/alembic/CLI contexts
- Scheduler has 23 registered jobs as of 2026-10-07 (shared basketball Games collector added)
- ML model artifacts (`app/ml_models/*.json`) are gitignored — regenerate with `flask retrain --force`

## Skills (load on demand)
- **[`taste`](.agents/skills/taste/SKILL.md)** — pinned, reviewed Taste compatibility guide for Flask/Jinja; dashboard work stays governed by frontend-design and definition-of-done
- **`ml-models`** — XGBoost architecture, calibration constants, auto-pick thresholds, retrain, odds API
- **`database`** — DB config, migrations, key model fields (Bet/BetPostmortem/PlayerGameLog), local DB scripts
- **`deployment-ci`** — GitHub Actions CI, Railway deployment, git workflow, gunicorn
- **`project-layout`** — Flask blueprints, services map, multi-sport expansion
- **`definition-of-done`** — UI breakpoints, live-progress rows, control regression checklist
