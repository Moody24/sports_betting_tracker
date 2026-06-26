# INFO Batch 1 Close-Out — Security Headers & One-Liners
*Completed: 2026-06-26*

## Outcome: COMPLETE

## Findings closed
| # | Description | Location |
|---|-------------|----------|
| 5 | CSP unsafe-inline documented | __init__.py:169 |
| 6 | HSTS header (prod-only) | __init__.py:179 |
| 7 | OCR upload mimetype check | bet_import.py:393 |
| 8 | espn_id alphanumeric guard | nba_live.py:451,472 |
| 22 | inject_user skips health endpoints | __init__.py:150 |
| 23 | RATELIMIT_STORAGE_URI inside create_app() | __init__.py:102 |
| 33 | cv=prefit comment corrected | pick_quality_model.py:287 |
| 37 | Bias N threshold comment | projection_engine.py:64 |
| 62 | Entrypoint already 100755 — no change needed | docker-entrypoint.sh |

## Merge info
- Worktree commit: efca734
- Merge to main: de025be

## Test result: 477/477 pass

## Agent pipeline result
Fixer: PASS · Reviewer: PASS · Updater: no corrections needed

## Remaining INFO findings (Batch 2)
#11 N+1 display_label, #13 grade_bet two-commit, #28 JSON schema,
#40 stale-job watchdog, #61 _resolve_card_progress extraction,
#44 setInterval handle, #45 parlay queue on logout, #46 innerHTML safety,
#35 stale defense data
