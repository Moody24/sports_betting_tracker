---
name: database
description: "INVOKE when working on SQLAlchemy models, database migrations, DB queries, model field names (Bet, BetPostmortem, PlayerGameLog), SQLite vs PostgreSQL config, or writing local DB scripts in Edge Tracker."
---

## Database Config

**Local dev:** SQLite — `DATABASE_URL=sqlite:///instance/app.db`
**Production:** PostgreSQL via `DATABASE_URL` in `.env` (Neon was used; currently disconnected)

- Migrations: `flask --app run.py db upgrade heads`
- `pool_size` / `max_overflow` are **PostgreSQL-only** — `__init__.py` skips them for SQLite automatically
- Tests use `sqlite:///:memory:` via `BaseTestCase.setUp()` — do NOT add QueuePool params in tests (gives each connection a separate in-memory DB and breaks all tests)

## Key Model Fields (avoid wrong-field errors)

**`Bet`**
- `outcome` (not `result`)
- `prop_line` (not `line` or `over_under`)
- `source='auto_generated'` for auto picks

**`BetPostmortem`**
- `projected_stat`, `actual_stat`, `stat_type`, `prop_line`
- Join to `Bet` for `prop_type` / `player_name`

**`PlayerGameLog`**
- `win_loss` → `'W'` or `'L'`
- `plus_minus` → float
- `team_abbr`, `home_away` → `'home'` or `'away'`

## Local DB Scripts (SQLite)

```bash
source .venv/bin/activate
python3 << 'PYEOF'
from app import create_app, db
app = create_app()
with app.app_context():
    result = db.session.execute(db.text("SELECT count(*) FROM player_game_log")).scalar()
    print("PlayerGameLog rows:", result)
PYEOF
```

- For PostgreSQL: `export $(grep -v '^#' .env | grep -v '^\s*$' | xargs) 2>/dev/null` before running
- `set -a && source .env` fails on `.env` lines containing `&` — always use the `export $(xargs)` form

## Alembic / Migrations Gotcha
Auto-generated merge migrations (`flask db merge`) always include unused imports:
```python
from alembic import op
import sqlalchemy as sa
```
**Delete both lines before committing** — ruff will fail on push if they remain.
