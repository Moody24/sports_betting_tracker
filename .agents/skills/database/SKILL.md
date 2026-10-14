---
name: database
description: Use for Edge Tracker SQLAlchemy models, migrations, queries, field names, and isolated local database work.
---

# Database

Local development uses project-scoped PostgreSQL. Start it through
`scripts/local_postgres.sh start` and run the app with
`.venv/bin/python scripts/run_local.py`. The launcher handles dotenv.
Do not use shell `export $(...xargs)`, copy a live `.env`, or point tests at
the real database. Test fixtures use SQLite; PostgreSQL migration parity is
checked separately in CI.

The executable field owner is `app/models.py`; schema history is
`migrations/versions/`. See
[the system contract](../../../docs/architecture/system-contract.md) and
[database maintenance](../../../docs/runbooks/db-maintenance.md).
Before migration, inspect the target, review generated operations and test
the full migration chain on a disposable database. Never cut over real data
as a side effect of a documentation or model edit.

Common field names: `Bet.outcome` and `Bet.prop_line` (not `result` or
`over_under`); `BetPostmortem.projected_stat`, `actual_stat`,
`stat_type`, `prop_line`; `PlayerGameLog.win_loss`, `plus_minus`,
`team_abbr`, `home_away`. Check the live model definition before coding.
Remove unused Alembic imports from generated merge revisions before linting.
