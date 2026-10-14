# Edge Tracker — project guidance

Edge Tracker is a modular Flask, SQLAlchemy, XGBoost and APScheduler NBA betting
tracker. Read [the system contract](docs/architecture/system-contract.md) before
changing modules, schema, APIs, caches, jobs or cross-module state.
[ARCHITECTURE.md](ARCHITECTURE.md) is the short onboarding map.
The source owners and tests take precedence over copied counts in old notes.
At task start, read the short [project memory](docs/development/project-memory.md)
for current context, then inspect the relevant code and tests. Update its linked
decision, change-evidence, or lesson records only with verified outcomes.

## Local commands

```bash
scripts/local_postgres.sh start
.venv/bin/python scripts/run_local.py
```

The launcher reads the project's environment. Its default disables scheduled
jobs; use `--with-scheduler` only when live jobs are intended. Do not source or
print credentials. The local setup uses project-scoped PostgreSQL; tests use
isolated fixtures. Check the actual configuration before running any command
that touches data.

```bash
SECRET_KEY=test .venv/bin/python -m unittest tests.test_system_contract -v
SECRET_KEY=test .venv/bin/python -m coverage run -m unittest discover -s tests -v
.venv/bin/python -m coverage report --include="app/*"
.venv/bin/ruff check .
.venv/bin/bandit -q -r app -x tests -ll
```

Use `unittest`. Run focused checks while editing and required CI gates before
integration. A worktree separates edits, not credentials or permissions.
For meaningful changes, follow the [bounded coding workflow](docs/development/coding-workflow.md):
assign file owners, exchange scoped handoffs, then review and test the integrated diff.

## Invariants

- Game dates, slate rollover, and freshness decisions use Eastern Time. Persist
  event timestamps as the contract specifies.
- Preserve ownership checks, authentication, CSRF, provider identity, quote
  provenance, freshness, idempotent writes and model validation gates.
- Provider data and model outputs must show honest stale or unavailable states.
  Never invent sports data, quote freshness, model performance or profitability.
- Keep Flask/Jinja and vanilla CSS/JavaScript. Preserve the current product
  typography, horizontal navigation, meaningful status colors and UI controls.
- Do not run live provider jobs, real database migrations, model retraining,
  deployment or external publishing as a side effect of a documentation task.
- Do not install or invoke Superpowers or Supabase for this project. Historical
  `docs/superpowers/` plans are records, not active workflow mandates.

## Task-specific skills

These project entrypoints are read when relevant; optional community skills
require an explicit request. If a client does not offer a project skill in its
picker, explicitly ask it to read the linked `SKILL.md` for that task. Do not
assume a matching global skill is the project adaptation. See
[stack provenance](docs/development/codex-stack.md).

| Task | Guidance |
| --- | --- |
| DB/schema | [database](.agents/skills/database/SKILL.md) |
| Model/data contracts | [ml-models](.agents/skills/ml-models/SKILL.md) |
| CI/release | [deployment-ci](.agents/skills/deployment-ci/SKILL.md) |
| Product UI | [interface-design](.agents/skills/interface-design/SKILL.md) and [definition-of-done](.agents/skills/definition-of-done/SKILL.md) |
| Optional Apple HIG lens | [apple-hig-reference](.agents/skills/apple-hig-reference/SKILL.md) on explicit request; secondary to product UI guidance |
| Public pages | [taste](.agents/skills/taste/SKILL.md) when requested |
| Responsibility review | [codebase-design](.agents/skills/codebase-design/SKILL.md) when requested |
| Bounded implementation | [ponytail](.agents/skills/ponytail/SKILL.md) when requested |
| HTML explanation | [visual-explainer](.agents/skills/visual-explainer/SKILL.md) when requested |
| Test-first behavior change | [tdd](.agents/skills/tdd/SKILL.md) when requested |

For shared UI files, assign one writer and verify all affected surfaces.
Report exact tests run, limitations and remaining risks; do not claim a full
suite passed from a focused check.
