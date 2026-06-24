# Edge Tracker — Sports Betting Tracker

A Flask web application for recording bets, tracking outcomes, projecting player props via XGBoost ML models, and understanding betting performance over time. Currently focused on NBA with architecture in place for multi-sport expansion.

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

## Tech Stack

- **Backend:** Flask, SQLAlchemy, Flask-Login, Flask-Migrate, Gunicorn
- **Frontend:** Jinja2 templates + Bootstrap
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
pip install -r requirements.txt
```

### 2. Create your `.env` file

```bash
cp .env.example .env
```

Minimum required values for local development:

```env
SECRET_KEY=<generate with: python -c "import secrets; print(secrets.token_hex(32))">
DATABASE_URL=sqlite:///instance/app.db
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

The container entrypoint runs migrations before starting Gunicorn.

## Odds API Setup

Live player props and market lines require an API key from [the-odds-api.com](https://the-odds-api.com) (free tier available). Set `ODDS_API_KEY` in your `.env`. Without it, the app still works — live odds pages will degrade gracefully.

## ML Models

Models are XGBoost regressors trained on `PlayerGameLog` data (37 features). Artifacts are stored locally at `app/ml_models/*.json` (gitignored).

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

Coverage gate: 75% current · 80% target (tracked in backlog). Test runner is **unittest** (not pytest).

## Linting

```bash
ruff check .
bandit -q -r app -x tests -ll
```

## Project Structure

See [`ARCHITECTURE.md`](ARCHITECTURE.md) for a full module map, data flow diagrams, and multi-sport expansion guide.

Key directories:

```
app/
├── routes/       Flask blueprints (auth, bets, NBA analysis)
├── services/     Business logic (NBA, ML, scheduler, odds, postmortems)
├── cli/          Flask CLI commands (retrain, health-report, market-recommend)
├── models.py     SQLAlchemy models (11 tables)
├── ml_models/    Local model artifact JSON files (gitignored)
└── templates/    Jinja2 HTML templates
docs/
├── ARCHITECTURE.md        System overview and expansion guide
├── runbooks/              Operational guides (DB, retrain, incident response)
└── postmortem_system.md   Bet diagnostic system
```

## Contributing

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/your-change`)
3. Commit your changes
4. Push your branch and open a pull request
