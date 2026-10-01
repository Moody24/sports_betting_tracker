# Edge Tracker — Sports Betting Tracker

A Flask web application for recording bets, tracking outcomes, projecting player props via XGBoost ML models, and understanding betting performance over time. Currently focused on NBA with architecture in place for multi-sport expansion.

## Project Status

The NBA application and local database path are implemented. The current
[launch audit](docs/audits/2026-10-01-local-launch-audit.md) separates verified
local behavior from the remaining season-opening and invited-beta gates. The
September release baseline was 1,326 unit tests at 85% application coverage
and 58 Playwright browser tests; current results belong in the dated audit.

Hosted deployment is intentionally inactive. A production launch still requires
operator-owned infrastructure, secrets, backups, monitoring, and legal/product
decisions. Model profitability is not claimed; promotion remains gated on licensed
real decision/closing lines and at least 400 eligible resolved picks. MLB and NFL
are separate future product scopes, not partially implemented features.

## Features

- User registration, login, and logout with secure password hashing
- Create, edit, and delete bets (single, parlay, props)
- Dashboard with betting history and key performance totals
- NBA prop projections via XGBoost models (points, rebounds, assists, threes, steals, blocks)
- Live player props and market odds via [The Odds API](https://the-odds-api.com)
- Live NBA scores via ESPN (no auth required)
- Automated bet grading and postmortem diagnostics
- ML model health monitoring and calibration drift detection
- Database migrations with Flask-Migrate (Alembic)
- Explicit authentication, ownership, CSRF, response, and rate-limit policy for every route
- Safe HTML/JSON error handling with request correlation
- Public methodology, data-source, privacy, terms, and responsible-gambling pages
- Guarded SQLite-to-PostgreSQL migration tooling and production readiness checks

## Tech Stack

- **Backend:** Flask, SQLAlchemy, Flask-Login, Flask-Migrate, Gunicorn
- **Frontend:** Jinja2 templates + owned CSS design system + vanilla JavaScript
- **Database:** PostgreSQL 16 for local and hosted operation; SQLite remains a supported import source
- **ML:** XGBoost, scikit-learn (model artifacts stored locally in `app/ml_models/`)
- **Odds:** The Odds API (player props, moneyline, totals)
- **Scores:** ESPN API (free, no auth)

## Quick Start (Local)

### 1. Clone and set up the virtualenv

```bash
git clone https://github.com/Moody24/sports_betting_tracker.git
cd sports_betting_tracker
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
```

### 2. Create your `.env` file

```bash
cp .env.example .env
```

Minimum required values for local development:

```env
SECRET_KEY=<generate with: python -c "import secrets; print(secrets.token_hex(32))">
DATABASE_URL=postgresql:///edge_tracker_local?host=/absolute/path/to/sports_betting_tracker/instance&port=5433
ODDS_API_KEY=<your key from the-odds-api.com — optional, needed for live lines>
FLASK_DEBUG=false
SCHEDULER_ENABLED=false
```

### 3. Start local PostgreSQL and run migrations

```bash
brew install postgresql@16
scripts/local_postgres.sh start
# Put the exact URL printed by scripts/local_postgres.sh url in .env.
flask --app run db upgrade heads
```

### 4. Start the app

```bash
.venv/bin/python scripts/run_local.py
```

App runs at `http://127.0.0.1:5000`. Pass `--with-scheduler` when the local
data jobs should run; that uses the same single worker and may consume Odds
API credits. The local launcher keeps the scheduler off unless that flag is
passed, regardless of the ambient `.env` value.

The project-scoped PostgreSQL files stay under ignored `instance/postgres/`.
`scripts/local_postgres.sh backup` writes a restricted custom-format dump under
`instance/backups/`; `scripts/local_postgres.sh stop` stops the database. A fresh checkout has no
historical rows or trained model artifacts; import/retrain those before
assessing projection quality.

> By default, startup does **not** auto-run migrations. Set `AUTO_DB_UPGRADE=true` only if you want boot-time migrations.

## Run with Docker (optional)

```bash
docker compose up --build
```

Run `flask --app run.py db upgrade heads` before starting a new container image.
Hosted services use the blocking pre-deploy migration command in `railway.toml`;
web and scheduler processes never race schema changes at startup.

## Odds API Setup

Live player props and market lines require an API key from [the-odds-api.com](https://the-odds-api.com) (free tier available). Set `ODDS_API_KEY` in your `.env`. Without it, the app still works — live odds pages will degrade gracefully.

## ML Models

Two models: **Model 1** (6 XGBoost regressors — points, rebounds, assists, threes, steals, blocks) trained on `HistoricalGameLog` data with a shared 30-feature builder, and **Model 2** (XGBoost pick-quality classifier, 21 features) that scores projection confidence. Artifacts are stored locally at `app/ml_models/*.json` (gitignored).

To retrain models locally:

```bash
source .venv/bin/activate
flask retrain --force
```

Check model health and calibration drift:

```bash
flask health-report
flask health-report --days 7
```

See `docs/runbooks/retrain.md` for the full retrain guide.

## Running Tests

```bash
source .venv/bin/activate
SECRET_KEY=test python -m coverage run -m unittest discover -s tests -v
python -m coverage report --include="app/*"
```

CI coverage gate: 80% · current application coverage: 85%. Test runner is
**unittest** (not pytest).

Current cleanup status and deliberately retained follow-up work are tracked in
[`docs/tech-debt-register.md`](docs/tech-debt-register.md).
The repository/external launch boundary is tracked in
[`docs/launch-readiness-and-expansion-todo.md`](docs/launch-readiness-and-expansion-todo.md).

Run the browser suite after installing the Node development dependencies and
Playwright Chromium:

```bash
npm ci
npx playwright install chromium
npm run test:e2e
```

The canonical combined release check runs linting, security and secret scans,
dependency auditing, the full unittest suite, and the coverage gate:

```bash
./scripts/predeploy_guardrails.sh
```

## Linting

```bash
ruff check .
bandit -q -r app -x tests -ll
```

## Project Structure

[`docs/architecture/system-contract.md`](docs/architecture/system-contract.md) is the
canonical architecture contract. See [`ARCHITECTURE.md`](ARCHITECTURE.md) for the
descriptive module map, data flows, and multi-sport expansion guide.

Key directories:

```
app/
├── routes/       Flask blueprints (auth, bets, NBA analysis)
├── services/     Business logic (NBA, ML, scheduler, odds, postmortems)
├── cli/          Flask CLI commands (retrain, health-report, market-recommend)
├── models.py     SQLAlchemy models (16 tables)
├── ml_models/    Local model artifact JSON files (gitignored)
└── templates/    Jinja2 HTML templates
docs/
├── architecture/
│   └── system-contract.md Canonical module and state ownership contract
├── runbooks/              Operational guides (DB, retrain, incident response)
└── postmortem_system.md   Bet diagnostic system
ARCHITECTURE.md             Descriptive system overview and expansion guide
```

## Contributing

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/your-change`)
3. Commit your changes
4. Push your branch and open a pull request
