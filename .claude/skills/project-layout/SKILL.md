---
name: project-layout
description: "INVOKE when navigating the Edge Tracker codebase, adding new services or routes, understanding the Flask blueprint structure, or working on multi-sport expansion."
---

## Directory Structure

```
app/
├── routes/         # Flask blueprints: auth, bet, main, nba_analysis, nba_live
├── services/       # Business logic (see below)
├── ml_models/      # XGBoost artifact JSON files (gitignored)
├── cli/            # Flask CLI commands package
│   ├── __init__.py             # Command registration
│   ├── model_commands.py
│   ├── market_commands.py
│   ├── stats_commands.py
│   └── observability_commands.py
├── static/         # CSS, JS, fonts, images
└── templates/      # Jinja2 HTML templates
tests/              # unittest test files (CI runs all)
migrations/         # Alembic DB migrations
```

## Key Services (`app/services/`)

| File | Responsibility |
|---|---|
| `scheduler.py` | APScheduler background jobs, auto-pick thresholds |
| `nba_service.py` | NBA implementation of SportService ABC |
| `ml_model.py` | Model loading and inference |
| `pick_quality_model.py` | XGBoost classifier for pick confidence |
| `postmortem_service.py` | Bet grading and postmortem diagnostics |
| `stats_service.py` | Player stat aggregation |
| `projection_engine.py` | Projection logic + calibration constants |
| `value_detector.py` | Edge detection against market lines |
| `ml_feature_builder.py` | **Canonical** 37-feature builder — shared by training and inference |
| `base.py` | `SportService` ABC + `SPORT_REGISTRY` |

## Multi-Sport Expansion
- `base.py` defines `SportService` ABC with 10 abstract methods (scoreboard, odds, props, bet resolution, etc.)
- `SPORT_REGISTRY: dict[str, SportService]` in `base.py` — register new sports here
- NBA is the only registered sport (`nba_service.py`)
- Step-by-step guide: `ARCHITECTURE.md` → Multi-Sport Expansion section

## Scheduler Guard
`_is_non_server_invocation()` in `app/__init__.py` — prevents APScheduler from starting in pytest, Alembic, or CLI contexts. Never remove this guard.
