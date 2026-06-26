# Phase 3 Close-Out — ML Pipeline Correctness
*Completed: 2026-06-25*

## Outcome: COMPLETE

## Findings closed
| # | Description | Fix location |
|---|-------------|-------------|
| 32 | FEATURE_KEYS assertion | ml_feature_builder.py:332 |
| 48 | games_last_7_days real date | Already wired Phase 1 — verified at ml_feature_builder.py:307 |
| 50 | Zero-fill warning at inference | ml_model.py:529–531 |
| 31 | Explicit sort before z-score slice | projection_engine.py:381–382 |

## What was done
- build_ml_features_from_history() now asserts feature key order matches FEATURE_KEYS before returning — catches index drift at construction time
- compute_schedule_density() confirmed to receive real game_date from Odds API start_time (Phase 1 wiring verified)
- predict_stat() logs missing feature names before zero-filling — silent failures now surfaced
- _compute_z_score() sorts logs by game_date (None-safe) and correctly takes most-recent N with [-last_n:]

## Merge info
- Worktree commit: 82f5e1d
- Merge commit on main: 087c066

## Test result
288/288 test_services pass · 776/776 full suite pass

## Agent pipeline result
Fixer: PASS (implemented correctly first try)
Reviewer: PASS (all verified on disk)
Updater: no corrections needed

## Final verification

```
/Users/mohamoudmohamed/sports_betting_tracker/app/services/ml_feature_builder.py:97:def compute_schedule_density(
/Users/mohamoudmohamed/sports_betting_tracker/app/services/ml_feature_builder.py:118:    """Return logs sorted by game_date, tolerating missing dates."""
/Users/mohamoudmohamed/sports_betting_tracker/app/services/ml_feature_builder.py:307:    features['games_last_7_days'] = float(compute_schedule_density(logs, current_game_date))
/Users/mohamoudmohamed/sports_betting_tracker/app/services/ml_feature_builder.py:332:    assert list(features.keys()) == FEATURE_KEYS, (
/Users/mohamoudmohamed/sports_betting_tracker/app/services/projection_engine.py:381:        logs = sorted(logs, key=lambda x: (getattr(x, 'game_date', None) is None, getattr(x, 'game_date', None) or _date.min))
/Users/mohamoudmohamed/sports_betting_tracker/app/services/projection_engine.py:481:        sorted_logs = sorted(logs, key=lambda lg: ((getattr(lg, 'game_date', None) is None), getattr(lg, 'game_date', None)))
/Users/mohamoudmohamed/sports_betting_tracker/app/services/projection_engine.py:482:        dates = {getattr(g, 'game_date', None) for g in sorted_logs[-10:] if getattr(g, 'game_date', None)}
/Users/mohamoudmohamed/sports_betting_tracker/app/services/ml_model.py:229:        logs = sorted(logs, key=lambda lg: ((lg.game_date is None), lg.game_date))
```

## Ready for Phase 4
Phase 4 target: 4 missing DB indexes in a single Alembic migration
Findings: #14 (user_id+created_at on Bet), #15 (external_game_id on Bet), #16 (cache_expires on PlayerGameLog), #17 (job_name on JobLog)
