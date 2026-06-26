# Phase 1 Close-Out — Merge & Status Updates
*Completed: 2026-06-25*

## Outcome: COMPLETE

## What was done
- Merged fix/30-days-rest-skew → main (was already up to date; fix code at HEAD 8207b2d)
- REVIEW_PLAN.md updated: #30/#47/#49 → Closed | #55/#56 → Disputed
- 776/776 tests verified pass on main post-merge

## Findings closed
| # | Verdict | Severity |
|---|---------|----------|
| 30 | Closed | HIGH |
| 47 | Closed | MEDIUM |
| 49 | Closed | LOW |
| 55 | Disputed (phantom) | BLOCKER — reclassified |
| 56 | Disputed (phantom) | BLOCKER — reclassified |

## Discrepancy log
- Fixer self-report incorrectly stated #30 severity as MEDIUM; actual REVIEW_PLAN.md row shows HIGH. File is correct; report corrected by Updater.

## Final verification

`grep -n "| 30 \|| 47 \|| 49 \|| 55 \|| 56 " REVIEW_PLAN.md` output (verbatim):

```
502:| 4 | R1 | `app/__init__.py` | 30 | MEDIUM | `default_limits=[]` — no global rate limit fallback. Routes without an explicit `@limiter.limit()` decorator are unprotected. Auth routes are covered but `nba_live.py`, `bet_crud.py`, `main.py` routes are likely uncovered. | Open |
528:| 30 | R5 | `app/services/projection_engine.py` | 443–470 | HIGH | **Train/inference skew — `days_rest` / `back_to_back`:** At inference time `_build_ml_features()` always passes `last_log_date + timedelta(days=1)` as `current_game_date`, making `days_rest` always exactly `1.0` and `back_to_back` always `1.0` — regardless of the real schedule. During training, true game dates are used and these features vary naturally across the full distribution (1–7+ days). This is a total distribution shift on 2 of 31 features affecting every live projection. Fix: add `game_date` parameter to `score_prop()`, `ProjectionEngine.project_stat()`, and thread real Odds API `start_time` date through to `_build_ml_features()`. Fixed in `fix/30-days-rest-skew` branch; merged to main 2026-06-25. | Closed |
545:| 47 | R5 | `app/services/value_detector.py`, `app/services/projection_engine.py` | 123, 459 | MEDIUM | **Fix for #30 requires API surface change — `score_prop()` has no `game_date` parameter.** The real scheduled game date (`start_time`) is available from the Odds API at `value_detector.py:459` but is discarded immediately (assigned only to `score['match_date']` for display). Even if `_build_ml_features()` were patched to accept an external date, plumbing it through requires adding a `game_date` parameter to `score_prop()` (`value_detector.py:123`) and `ProjectionEngine.project_stat()`. The fix for #30 is a multi-signature refactor, not a local one-liner. Confirmed by inter-agent review 2026-06-24. | Closed |
547:| 49 | R5 | `app/services/projection_engine.py` | 459 | LOW | **Residual inline import inside `_build_ml_features()`.** The fix for #30 removed the dead-code double-computation blocks (lines 443–458) but left `from datetime import timedelta` as an inline import at line 459 inside the `game_date is None` fallback block. Module-level import covers `_date` but not `timedelta`, so the inline import is load-bearing and executes every time the fallback fires. Inconsistent with the project's module-level import style; invisible to ruff. Fix: add `timedelta` to the top-of-file import: `from datetime import date as _date, timedelta`. Severity escalated INFO → LOW after inter-agent review 2026-06-24. | Closed |
557:| 55 | R9 | `docker-entrypoint.sh` | 19 | BLOCKER | **`endif` syntax error — container cannot start.** Script uses `#!/usr/bin/env sh` but closes the `if` block with `endif` (csh/tcsh syntax). POSIX sh requires `fi`. The shell fails to parse the script; with `set -e`, the container exits before gunicorn starts. Railway deployment is currently inactive (CLAUDE.md), so this has never been caught in production. Fix: change `endif` → `fi` on line 19. | Disputed |
558:| 56 | R9 | `docker-entrypoint.sh` | 8–17 | BLOCKER | **Undefined variable `${MIGRATE_CMD}` — migrations silently skipped.** Python migration code is stored in `MIGRATION_TIMEOUT` (lines 8–12), but `timeout` and `python -c` on lines 14/17 reference `${MIGRATE_CMD}` (never defined). In sh, undefined variables expand to empty string, so the command becomes `python -c ""` — succeeds and does nothing. Even after fixing #55, schema migrations would never run. Fix: rename `MIGRATION_TIMEOUT` → `MIGRATE_CMD` to match the usage sites. | Disputed |
```

Note: line 502 matches finding #4 (which references line 30 in the source file, not finding #30) — this is expected grep collateral from the column value "30". Findings #30, #47, #49, #55, and #56 all show their correct status (Closed or Disputed) as verified.

## Ready for Phase 2
Phase 2 targets: #24 (outcome validation), #26/#27 (bounds validation), #4 (rate limit fallback), #20 (RATELIMIT_STORAGE_URI docs)
Files: app/routes/bet_import.py, app/__init__.py, .env.example
