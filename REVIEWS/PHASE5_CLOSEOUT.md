# Phase 5 Close-Out — Scheduler & Infrastructure
*Completed: 2026-06-25*

## Outcome: COMPLETE

## Findings closed
| # | Description | Fix location |
|---|-------------|-------------|
| 38 | US/Eastern deprecated alias removed | time_helpers.py:6, scheduler.py APP_TIMEZONE |
| 60 | ZoneInfo duplication across 5+ modules | 9 files now import ET from time_helpers |
| 39 | injury_am simultaneous firing at 10:00 | scheduler.py:1243 — now minute=5 |
| 42 | resolve_and_grade two-commit atomicity gap | scheduler.py:816/830/846 — savepoints + single commit |
| 57 | docker-entrypoint migration failure non-fatal | docker-entrypoint.sh — exit 124=warn, else fatal |

## What was done
- Single ET constant defined in time_helpers.py; all consumers updated to import it
- APScheduler timezone string changed from "US/Eastern" (deprecated) to "America/New_York" (IANA canonical)
- injury_am job staggered 5 minutes after stats_refresh to prevent simultaneous DB contention
- resolve_and_grade now atomic: per-write savepoints + one final commit; crash before commit rolls back entirely
- docker-entrypoint.sh now distinguishes timeout (RC=124, non-fatal) from real migration errors (fatal)

## Merge info
- Worktree commit: ab5a118
- Merge to main: bed187f

## Test result
320/320 pass

## Agent pipeline result
Fixer: PASS (all 4 fixes applied correctly first try)
Reviewer: PASS (all verified on disk, no corrections needed)
Updater: no corrections needed

## Final verification

```
$ grep -rn "US/Eastern" /Users/mohamoudmohamed/sports_betting_tracker/app/ | grep -v __pycache__
(no output — zero remaining references)

$ grep -n "ET\s*=" /Users/mohamoudmohamed/sports_betting_tracker/app/utils/time_helpers.py
6:ET = ZoneInfo("America/New_York")
7:_ET = ET  # backward-compat alias

$ grep -n "minute=5" /Users/mohamoudmohamed/sports_betting_tracker/app/services/scheduler.py
1243:        CronTrigger(hour=10, minute=5, timezone=APP_TIMEZONE),
```

All three checks confirm the fixes are in place on disk.

## Ready for Phase 6
Phase 6 targets: test coverage to ≥80% CI gate
Findings: #51 (scheduler/nba_service/nba_live/model_commands gaps), #53 (db.session.execute mock replacement)
Files: tests/test_services.py, tests/test_routes.py
Current coverage: ~75% (8166 stmts, 2051 miss)
