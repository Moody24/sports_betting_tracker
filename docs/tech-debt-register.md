# Technical Debt Register

Last audited: 2026-09-03

This is the current engineering-health baseline for Edge Tracker. It records
deliberately retained debt; completed cleanup belongs in Git history rather
than remaining as an open checklist.

## Current baseline

- Canonical guardrail: `scripts/predeploy_guardrails.sh`
- Test runner: `unittest`
- Full verification: 1,265 tests passing, 80% application coverage
- Static/security gates: Ruff, Bandit, detect-secrets, Git-history credential
  scanning, and pip-audit passing
- Ruff C901 inventory: 44 functions, down from 59 before this cleanup
- Working tree expectation: no generated files; personal untracked files are
  outside project scope

The generic debt scanner is not used as a trend KPI. Its duplicate detector
counts generated coverage data and repeated documentation fragments, producing
too many false positives. Ruff C901, tests, coverage, Bandit, dependency checks,
and architecture-contract tests are the reproducible measures for this repo.

## Resolved in the 2026-09-03 cleanup

- Removed dead UI assets and centralized display configuration.
- Enforced route/service dependency boundaries with architecture tests.
- Centralized ESPN transport and prop-boxscore parsing.
- Extracted bet placement, projection, auto-pick, postmortem, app startup,
  pending grading, value scanning, pick-quality inference, stat-analysis,
  bet-editing, and context-flag workflows into focused helpers/services.
- Fixed cached-score mutation in stat-analysis rendering.
- Fixed pick-quality local fallback lookup when a global model is selected.
- Removed the duplicate pytest invocation from pre-deploy checks.
- Split development-only coverage tooling from production dependencies.
- Eliminated all 35 known runtime dependency findings and made dependency,
  environment, and credential scans blocking local/CI gates.
- Established a one-worker hosted baseline, fail-closed production limiter
  topology, and blocking Railway pre-deploy migrations.
- Decomposed the two most complex workflows: market-model walk-forward
  evaluation and the all-player-props API. The latter now uses the required ET
  calendar date instead of the host machine's local date.
- Consolidated two conflicting inactive Railway runbooks.
- Blocked accidental external HTTP in the shared test fixture.
- Moved stale virtualenvs and generated test/browser artifacts to Trash,
  reclaiming about 525 MB.

## Accepted follow-up debt

| Priority | Item | Evidence and exit condition |
|---|---|---|
| P1 | Remaining high-complexity workflows | 44 C901 findings remain. Continue with `stats_commands.cli_backfill_player_logs` (22), then the 20-complexity pick-context, prop-import, and NBA ingestion/fetch workflows. Preserve behavior with focused tests per extraction. |
| P2 | Oversized service test module | `tests/test_services.py` is 7,613 lines across 55 test classes. Split by service only in a dedicated mechanical commit; do not mix that move with production changes. |
| P2 | Historical local model artifacts | `app/ml_models/` contains 190 ignored artifacts (about 76 MB). Active database metadata still references July artifacts, so retain them until `flask retrain --force` succeeds and the active paths are re-audited. |
| P2 before hosted deployment | Inactive Railway scripts and configuration | They are intentionally retained for restoration work and the surviving runbook is explicitly marked inactive. Revalidate or delete them when a hosting decision is made. |

## Next cleanup order

1. Refactor the six highest remaining C901 workflows with parity tests.
2. Split `tests/test_services.py` mechanically by domain.
3. Retrain models, verify active artifact paths, then prune unreferenced files.
4. Re-run the canonical guardrail and update this register's dated baseline.
