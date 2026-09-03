# Edge Tracker — Sports Betting Tracker

A Flask web application for recording bets, tracking outcomes, projecting player props via XGBoost ML models, and understanding betting performance over time. Currently focused on NBA with architecture in place for multi-sport expansion.

## Project Status

The repository-owned NBA scope is complete: the application, Sheet UI, security
contracts, migration tooling, test coverage, and technical-debt cleanup have been
implemented and verified. The current release baseline passes 1,326 unittests at
85% application coverage and 58 Playwright browser tests.

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
- **Database:** SQLite for local dev · PostgreSQL when deploying to a hosted environment
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
DATABASE_URL=sqlite:///app.db
ODDS_API_KEY=<your key from the-odds-api.com — optional, needed for live lines>
FLASK_DEBUG=true
SCHEDULER_ENABLED=false
```

### 3. Run migrations

```bash
flask --app run.py db upgrade heads
```

### 4. Start the app

```bash
flask run
# or: python run.py
```

App runs at `http://localhost:5000`.

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
