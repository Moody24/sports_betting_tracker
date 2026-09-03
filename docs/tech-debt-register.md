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
- Ruff C901 inventory: 14 functions, down from 59 before this cleanup
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
- Decomposed historical player-log backfill orchestration while preserving
  retries, resume checks, batched writes, dry runs, and optional retraining.
- Split provider-neutral prop import validation into focused timing, market,
  identity, and persistence steps while preserving idempotency.
- Decomposed historical pick-context backfill into explicit inference, scoring,
  feature-building, and persistence stages.
- Decomposed current odds parsing, historical game-snapshot backfill, and
  provider historical-market ingestion, with tests for partial bookmaker data,
  bet-derived enrichment, and force/no-force updates.
- Split the 7,613-line service test monolith into seven domain modules plus a
  shared fixture module. All 489 migrated test identities were retained and
  passed together.
- Retrained all six projection models and seven distributional heads or
  calibrators against 79,603 permanent historical rows. Retained 17 active and
  17 rollback artifacts, and moved 163 unreferenced artifacts (58.3 MB) to
  Trash after verifying every metadata path.
- Decomposed the synthetic-line backtest and pollution cleanup commands, with
  direct quantile, Poisson, deletion, deactivation, and retraining tests.
- Decomposed leakage-safe rolling backtests and daily prop snapshot ingestion,
  including failure persistence, incumbent fallback, duplicate suppression,
  and scheduled/decision/close timing tests.
- Decomposed single/batch live progress, team-usage feature construction, and
  prop scoring across Model 1, Model 2, and scenario signals. Batch progress
  now has an explicit one-summary-fetch-per-game contract test.
- Decomposed model-status reporting and multi-book prop parsing while
  preserving drift warnings, partial market data, consensus lines, and
  book-specific quotes.
- Consolidated two conflicting inactive Railway runbooks.
- Blocked accidental external HTTP in the shared test fixture.
- Moved stale virtualenvs and generated test/browser artifacts to Trash,
  reclaiming about 525 MB.

## Accepted follow-up debt

| Priority | Item | Evidence and exit condition |
|---|---|---|
| P1 | Remaining high-complexity workflows | 14 C901 findings remain. Market reporting, context-flag normalization, analysis presentation, and today's-game snapshot synchronization are resolved; continue with model-training, import, and scheduler workflows. Preserve behavior with focused tests per extraction. |
| P2 before hosted deployment | Inactive Railway scripts and configuration | They are intentionally retained for restoration work and the surviving runbook is explicitly marked inactive. Revalidate or delete them when a hosting decision is made. |

## Next cleanup order

1. Continue reducing the remaining C901 inventory in focused commits.
2. Re-run the canonical guardrail and update this register's dated baseline.
