# Edge Tracker — Architecture

## Overview

Edge Tracker is a Flask application for tracking sports bets, projecting player props using XGBoost ML models, and grading bets automatically against live results. It is currently NBA-focused and runs locally, with the codebase designed for multi-sport expansion.

---

## System Diagram

```
┌─────────────────────────────────────────────────────────────────────┐
│                         Edge Tracker (Local)                        │
│                                                                     │
│  Browser                                                            │
│    │                                                                │
│    ▼                                                                │
│  Flask App (run.py → app/__init__.py::create_app())                 │
│    │                                                                │
│    ├── app/routes/          ← HTTP request handling                 │
│    │     auth.py            ← register / login / logout             │
│    │     main.py            ← dashboard                             │
│    │     bet.py             ← bet detail view                       │
│    │     bet_crud.py        ← create / edit / delete                │
│    │     bet_import.py      ← OCR receipt import                    │
│    │     nba_analysis.py    ← player analysis pages                 │
│    │     nba_live.py        ← live NBA today page                   │
│    │                                                                │
│    ├── app/services/        ← business logic layer                  │
│    │     nba_service.py     ← ESPN + Odds API integration (1300 ln) │
│    │     ml_model.py        ← XGBoost Model 1 (stat projections)    │
│    │     pick_quality_model.py ← XGBoost Model 2 (bet quality)      │
│    │     ml_feature_builder.py ← 37-feature extractor (shared)      │
│    │     projection_engine.py  ← bias-corrected projection logic    │
│    │     postmortem_service.py ← settled bet diagnostics            │
│    │     market_recommender.py ← auto pick generation               │
│    │     value_detector.py     ← edge detection                     │
│    │     stats_service.py      ← game stat aggregation              │
│    │     matchup_service.py    ← matchup analysis                   │
│    │     context_service.py    ← PickContext builder                 │
│    │     scheduler.py          ← APScheduler background jobs        │
│    │     model_storage.py      ← local / S3 artifact storage        │
│    │     score_cache.py        ← game score caching                 │
│    │     base.py               ← SportService ABC + SPORT_REGISTRY  │
│    │                                                                │
│    ├── app/cli/             ← Flask CLI commands                    │
│    │     model_commands.py  ← retrain, health-report, calibration   │
│    │     market_commands.py ← market-recommend, odds commands       │
│    │     stats_commands.py  ← stat analysis                         │
│    │     observability_commands.py ← monitoring, pollution-report   │
│    │                                                                │
│    ├── app/models.py        ← SQLAlchemy ORM (11 tables)            │
│    └── app/ml_models/       ← local model artifacts (gitignored)    │
│                                                                     │
│  SQLite (instance/app.db)   ← local database                       │
│                                                                     │
└─────────────────────────────────────────────────────────────────────┘
           │                            │
           ▼                            ▼
   ESPN API (free)            The Odds API (ODDS_API_KEY)
   Live scores, box scores    Moneyline, totals, player props
```

---

## Database Schema (11 Tables)

| Table | Purpose |
|-------|---------|
| `user` | User accounts, bankroll, unit size |
| `bet` | All bets — single, parlay, prop |
| `bet_postmortem` | Post-settlement diagnostics per bet |
| `player_game_log` | NBA player stat history (per game) |
| `game_snapshot` | Game context: OT, blowout, final scores |
| `odds_snapshots` | Market line history per game |
| `team_defense_snapshot` | Team defensive metrics per season |
| `injury_report` | Player injury statuses |
| `pick_context` | Pregame ML context stored per bet |
| `model_metadata` | ML model registry with performance metrics |
| `job_log` | Scheduler job run history |

Migrations are managed by Alembic via Flask-Migrate. 27 versions tracked in `migrations/versions/`.

---

## ML Pipeline

```
PlayerGameLog rows
        │
        ▼
ml_feature_builder.py::build_features()
  └── 37 features: minutes, FGA, FG3A, opponent pace,
      defensive metrics, injury status, home/away, etc.
        │
        ├── Model 1 (ml_model.py)
        │     6 XGBoost regressors (one per stat type)
        │     Output: projected stat value (continuous)
        │     Bias-corrected in projection_engine.py:
        │       COMBO_PROP_BIAS_CORRECTION = +3.2 (PRA)
        │       SINGLE_STAT_BIAS_CORRECTION = {assists: +0.5, ...}
        │
        └── Model 2 (pick_quality_model.py)
              XGBoost classifier
              Input: 37 features + minutes_volatility + stat_attempts_volatility
              Output: confidence tier (strong / medium / low)
```

**CRITICAL:** `FEATURE_KEYS` in `ml_feature_builder.py` must be identical between training and inference. A mismatch causes silent prediction errors.

**Model artifacts** are stored locally at `app/ml_models/*.json` (gitignored). Run `flask retrain --force` to generate them.

---

## Background Jobs (APScheduler)

The scheduler runs only when `SCHEDULER_ENABLED=true`. Locally, keep it `false` and trigger jobs manually via CLI.

| Schedule | Job | What it does |
|----------|-----|-------------|
| 1:00 AM ET | `resolve_and_grade()` | Grade settled bets, create postmortems |
| 10:30 AM ET | `retrain()` | Retrain XGBoost models |
| Every 15 min | `nba_api_refresh()` | Refresh scores and player stats from ESPN |
| Every 5 min | `market_recommender()` | Generate auto prop picks |

Guard: `_is_non_server_invocation()` in `app/__init__.py` prevents the scheduler from starting in pytest, Alembic, or CLI contexts.

---

## Bet Lifecycle

```
1. Created       → User creates bet via /bets/new or auto-generated by market_recommender
2. Pending        → Bet has no outcome; PlayerGameLog not yet populated for game date
3. Graded         → scheduler.resolve_and_grade() matches final box score to prop line
4. Postmortem     → postmortem_service.py creates BetPostmortem with reason codes
                    (volume_spike, efficiency_drop, role_change, injury_impact, etc.)
5. Reviewed       → User views postmortem on bet detail page
```

---

## Data Flow: NBA Live Page

```
/bets/nba_today
      │
      ├── nba_service.fetch_scoreboard()       ← ESPN live scores
      ├── nba_service.fetch_odds_combined()    ← The Odds API moneyline/totals
      ├── nba_service.fetch_odds_events()      ← The Odds API event IDs
      └── nba_service.fetch_player_props()     ← The Odds API player prop lines
            │
            ▼
      projection_engine.project_stat()         ← XGBoost Model 1 projection
            │
            ▼
      value_detector.compute_edge()            ← edge = projection vs prop line
            │
            ▼
      Rendered in nba_live template with live clock, stat, edge, confidence tier
```

---

## Multi-Sport Expansion

The codebase is designed for multiple sports via the `SportService` abstract base class in `app/services/base.py`.

### How to add a new sport (e.g. NFL)

**Step 1 — Implement the service**

Create `app/services/nfl_service.py`:

```python
from app.services.base import SportService, SPORT_REGISTRY

class NFLService(SportService):
    @property
    def sport_key(self) -> str:
        return "nfl"

    @property
    def display_name(self) -> str:
        return "NFL"

    def fetch_scoreboard(self, date_str=None) -> list[dict]:
        # ESPN NFL scoreboard endpoint
        ...

    def fetch_boxscore(self, game_id: str) -> dict: ...
    def fetch_odds_combined(self) -> tuple[dict, dict]: ...
    def fetch_odds_events(self) -> dict: ...
    def fetch_upcoming_games(self) -> list[dict]: ...
    def fetch_player_props(self, event_id: str) -> dict: ...
    def get_todays_games(self) -> list[dict]: ...
    def get_player_props_for_game(self, game_id, games=None) -> dict: ...
    def resolve_pending_bets(self, pending_bets) -> list[tuple]: ...
    def get_prop_markets(self) -> list[str]: ...

SPORT_REGISTRY["nfl"] = NFLService()
```

**Step 2 — Add routes**

Create `app/routes/nfl_analysis.py` following the pattern of `nba_analysis.py` and `nba_live.py`. Register the blueprint in `app/__init__.py`.

**Step 3 — Add DB migrations (if needed)**

If the sport requires sport-specific tables (e.g. `nfl_player_game_log`), create a migration:

```bash
flask --app run.py db migrate -m "add nfl player game log table"
flask --app run.py db upgrade heads
```

**Step 4 — Wire into scheduler**

In `scheduler.py`, import and call `SPORT_REGISTRY["nfl"]` where relevant (score refresh, auto-grade, retrain trigger).

**Step 5 — The Odds API sport key**

The Odds API sport key for NFL is `americanfootball_nfl`. Update `fetch_odds_combined()` and `fetch_odds_events()` calls accordingly.

### Supported Odds API Sport Keys (reference)

| Sport | The Odds API key |
|-------|-----------------|
| NBA | `basketball_nba` |
| NFL | `americanfootball_nfl` |
| MLB | `baseball_mlb` |
| NHL | `icehockey_nhl` |
| NCAAB | `basketball_ncaab` |
| NCAAF | `americanfootball_ncaaf` |

---

## Key CLI Commands

```bash
# Model management
flask retrain --force                        # force retrain all models
flask health-report                          # model age + calibration drift
flask health-report --days 7                 # drift over last 7 days
flask evaluate-calibration --days 14         # projection accuracy report

# Data quality
flask pollution-report                       # audit data quality issues
flask pollution-report --fix                 # fix issues in place

# Bet diagnostics
flask backfill-postmortems --days 30         # create postmortems for old bets
flask backfill-postmortems --dry-run         # preview without writing
flask postmortem-report --days 30            # reason code distribution

# Market
flask market-recommend --date 2026-06-24     # run recommender for a specific date
```

---

## Environment Variables (Local Dev)

```env
# Required
SECRET_KEY=<generate: python -c "import secrets; print(secrets.token_hex(32))">
DATABASE_URL=sqlite:///instance/app.db

# Odds API (optional for local dev, needed for live lines)
ODDS_API_KEY=<from the-odds-api.com>

# Flask
FLASK_DEBUG=true
SCHEDULER_ENABLED=false          # keep false locally; trigger jobs via CLI

# ML model storage (local by default)
MODEL_STORAGE=local              # no S3 needed locally

# Auto-pick thresholds (optional overrides)
AUTO_PICK_MAX_TOTAL=50
AUTO_PICK_MIN_EDGE_STRAIGHT=0.15
AUTO_PICK_MIN_GAMES=15
AUTO_PICK_CONFIDENCE_TIER=strong
```

---

## Testing

```bash
# Run full test suite with coverage
source .venv/bin/activate
SECRET_KEY=test python -m coverage run -m unittest discover -s tests -v
python -m coverage report --include="app/*"

# Lint
ruff check .
bandit -q -r app -x tests -ll
```

Tests use `sqlite:///:memory:` — no external DB required.

---

## Operational Runbooks

| Runbook | Location |
|---------|---------|
| ML model retrain | `docs/runbooks/retrain.md` |
| Database maintenance | `docs/runbooks/db-maintenance.md` |
| Incident response | `docs/runbooks/incident-response.md` |
| Railway deployment (inactive) | `docs/runbooks/deployment.md` |
| Bet postmortem system | `docs/postmortem_system.md` |
| UI QA checklist | `docs/ui_v1_baseline.md` |
