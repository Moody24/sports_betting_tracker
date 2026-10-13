# Edge Tracker — Sports Betting Tracker

A Flask web application for recording bets, tracking outcomes, projecting player props via XGBoost ML models, and understanding betting performance over time. Currently focused on NBA with architecture in place for multi-sport expansion.

## Project Status

The NBA application and local database path are implemented. The current
[launch audit](docs/audits/2026-10-07-launch-baseline.md) separates verified
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
- Shared BALLDONTLIE score feed with visible freshness and reconnecting browser updates
- Legacy ESPN adapter with explicit source approval
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
- **Scores:** optional BALLDONTLIE Games; separate ESPN adapter disabled until permission is documented

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
.venv/bin/python scripts/run_local.py --with-scheduler
```

App runs at `http://127.0.0.1:5000` with one scheduler owner. The jobs may
consume Odds API credits. Omit `--with-scheduler` when only the web UI is
needed; the local launcher keeps jobs off in that mode regardless of `.env`.

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

## Source availability

Manual bet recording and performance review work without a sports-data feed.
ESPN and ESPN-derived remote release downloads are disabled by default. Enable
them only after establishing permission for your use and audience, then record
`ESPN_ACCESS_APPROVED=true` and `ESPN_RIGHTS_BASIS` in your local environment.
An unauthenticated URL is not permission to collect or redistribute its data.
See the [October 7 source review](docs/audits/2026-10-07-data-source-review.md)
and [launch baseline](docs/audits/2026-10-07-launch-baseline.md).

Source status distinguishes a recent successful fetch, stale data, and no
verified current data. Refreshing a page does not make its data fresh. Existing
APScheduler jobs poll and reconcile internal state; no external provider
webhook is configured or claimed. Do not enable multiple scheduler owners.

### Shared basketball score feed

After approving a free BALLDONTLIE account and securely configuring
`BALLDONTLIE_API_KEY`, run migrations and one scheduler owner. NBA Today reads
the shared Games snapshot. A blank key disables collection; no account or key
is created automatically. The adapter uses one request every 30 seconds
(2/minute against the documented free 5/minute limit), with persisted backoff
and `Retry-After`. It does not include odds or player box scores.

Authenticated browsers share a database reader and receive changed snapshots
through server-sent events. There is no provider websocket or webhook in this
path. Fetches within 75 seconds show as current; errors or older fetches show
stale for at most 15 minutes on the same ET day, then unavailable. The timestamp
is our successful fetch time; provider delay is not guaranteed.

The web baseline is one Gunicorn `gthread` worker with 16 threads and at most
8 concurrent streams. Streams heartbeat every 15 seconds and reconnect after
5 minutes to recheck authentication. A 100-event durable journal supports replay
and reset; hidden tabs close their streams. More web workers require a shared
rate limiter and capacity review. Proxies must allow unbuffered SSE; verify
that behavior in the target hosting environment before launch. Use one scheduler
owner regardless of the number of web processes.

BALLDONTLIE event IDs remain namespaced; this separate score board does not
silently grade legacy ESPN-linked bets. A verified crosswalk and independent
result verification are required before that integration. Odds quotas and market
coverage remain separate decisions. Manual tracking needs no new account.

## Expansion backlog

The [NFL/NHL roadmap](docs/audits/2026-10-07-sport-expansion-roadmap.md) starts only
after the basketball baseline is verified. UFC remains exploratory. Source
permissions, sport-specific schemas and evaluation gates precede any training;
these sports are not exposed as working product tabs.

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

## Historical Matchup Research

The read-only matchup screen compares a player's results with their previous
ten games and the opponent's previous ten defensive box scores. It discovers
patterns in 2023–24 and 2024–25, then checks them in 2025–26:

```bash
.venv/bin/python -m scripts.matchup_pattern_research \
  --output-dir /absolute/path/outside/this/repository
```

The report and CSVs include repeated matchups, defensive profiles, schedule
effects, and final-score associations. They are exploratory: historical
player-prop quotes are not yet in the local database, and box scores cannot
identify tactical coverage schemes. Keep research output outside this repository.

## Running Tests

```bash
source .venv/bin/activate
SECRET_KEY=test python -m coverage run -m unittest discover -s tests -v
python -m coverage report --include="app/*"
```

CI coverage gate: 80% · application coverage at the October 1 audit: 82%. Test runner is
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
├── models.py     SQLAlchemy models (22 tables)
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

Direct NBA Stats network collection also requires separate `NBA_ACCESS_APPROVED=true`
and `NBA_RIGHTS_BASIS`. ESPN permission does not authorize NBA Stats collection.
