# Edge Tracker — Full Fix Plan
*Audited 2026-06-25 · Sessions 1–3 inter-agent review + ground-truth file verification*

---

## Audit Corrections (Close These First)

These findings are in REVIEW_PLAN.md as "Open" but are either already fixed or verified phantom.

| # | Verdict | Evidence |
|---|---------|----------|
| 30 | **FIXED** — on `fix/30-days-rest-skew` branch | `game_date` param threaded through `value_detector.score_prop()` → `projection_engine.project_stat()` → `_build_ml_features()`; 776/776 tests pass |
| 47 | **FIXED** — same branch as #30 | `score_prop()` now accepts `game_date` and passes the real Odds API `start_time` date |
| 49 | **FIXED** — same branch | `timedelta` moved to module-level import in `projection_engine.py` |
| 55 | **PHANTOM** — close as Disputed | `docker-entrypoint.sh` uses `fi` (POSIX) at line 20, not `endif`. Verified by direct file read 2026-06-25. |
| 56 | **PHANTOM** — close as Disputed | Variable is named `MIGRATE_CMD` at line 8 and correctly referenced at lines 15/18. No undefined variable. Verified by direct file read 2026-06-25. |

**Action:** After merging the fix branch, update REVIEW_PLAN.md to mark #30/#47/#49 as Closed and #55/#56 as Disputed.

---

## Priority 1 — MEDIUM (Fix Before Next Merge)

### #24 · `app/routes/bet_import.py` — Outcome not validated in `manual_parlay()`

**What:** `manual_parlay()` accepts `outcome` from client JSON with no validation. A client can POST `{"outcome": "win"}` and create pre-graded bets, inflating P/L and win-rate stats.

**Fix:**
```python
# bet_import.py — inside manual_parlay(), after outcome = data.get(...)
if outcome != Outcome.PENDING.value:
    return jsonify({"success": False, "message": "New bets must be PENDING"}), 400
```

**File:** `app/routes/bet_import.py` · search for `outcome = data.get("outcome")`

---

### #32 · `app/services/ml_feature_builder.py` — `FEATURE_KEYS` not enforced at runtime

**What:** `build_ml_features_from_history()` manually builds the return dict. If a future feature insertion mismatches dict insertion order vs `FEATURE_KEYS` order, XGBoost silently maps wrong features to wrong indices. No error is raised.

**Fix:** Add assertion at the bottom of `build_ml_features_from_history()`:
```python
assert list(features.keys()) == FEATURE_KEYS, (
    f"Feature key mismatch: got {list(features.keys())}"
)
return features
```

**File:** `app/services/ml_feature_builder.py` · end of `build_ml_features_from_history()`

---

### #53 · `tests/test_services.py` — Prohibited `db.session.execute` mocks

**What:** Lines 3274 and 3288 use `patch.object(db.session, 'execute', side_effect=Exception(...))` — directly mocking SQLAlchemy internals. This violates the project convention that caused mock/prod divergence (session feedback).

**Fix:** Replace both mock patches with an in-memory SQLite DB with a deliberately closed connection:
```python
# Create a real engine, then immediately close its connection pool
engine = create_engine("sqlite:///:memory:")
engine.dispose()
# Monkeypatch db.engine with this broken engine for the duration of the test
```

**File:** `tests/test_services.py` lines ~3274 and ~3288 (health-check test class)

---

### #4 · `app/__init__.py` — No global rate limit fallback

**What:** `default_limits=[]` means routes without an explicit `@limiter.limit()` decorator are completely unprotected. `nba_live.py`, `bet_crud.py`, `main.py` routes are uncovered.

**Fix:** Change the limiter initialization:
```python
limiter = Limiter(
    key_func=get_remote_address,
    default_limits=["200/hour", "50/minute"],  # global fallback
    ...
)
```
Then audit which routes need tighter limits and add explicit decorators there.

**File:** `app/__init__.py` · Limiter initialization

---

### #12 · `app/__init__.py` — SQLite write-lock timeout not configured

**What:** APScheduler background jobs (e.g. `refresh_player_stats`) can exceed the 5s default SQLite file-lock timeout, causing `OperationalError: database is locked` on concurrent web writes. Railway PostgreSQL is unaffected.

**Fix:** In the SQLite-specific engine options branch:
```python
connect_args={'timeout': 30}
```

**File:** `app/__init__.py` · `SQLALCHEMY_ENGINE_OPTIONS` SQLite branch

---

### #20 · `app/__init__.py` — Per-worker rate limit counters in multi-process gunicorn

**What:** `RATELIMIT_STORAGE_URI` defaults to `memory://`. With `WEB_CONCURRENCY=2` each worker has its own counter — the effective rate limit doubles per client.

**Fix:**
1. Add to `.env.example`:
   ```
   # RATELIMIT_STORAGE_URI=redis://localhost:6379/0  # required for multi-worker prod
   ```
2. Document the multi-worker risk in the `create_app()` comment.
3. For Railway: configure a Redis add-on and set the env var.

**File:** `.env.example` (document) + `app/__init__.py` (add startup warning if `WEB_CONCURRENCY > 1` and URI is `memory://`)

---

### #57 · `docker-entrypoint.sh` — Migration failure non-fatal

**What:** Both branches use `|| echo "WARNING: ..."` — gunicorn starts even if migrations fail. A deploy with a broken migration silently starts with stale schema.

**Fix:** Differentiate timeout (non-fatal) from real error (fatal):
```sh
if command -v timeout >/dev/null 2>&1; then
  timeout "${MIGRATION_MAX_SECONDS}"s python -c "${MIGRATE_CMD}" \
    || { RC=$?; [ $RC -eq 124 ] && echo "WARNING: Migration timed out" || exit $RC; }
else
  python -c "${MIGRATE_CMD}"
fi
```
Exit code 124 = timeout; any other non-zero = real failure, abort startup.

**File:** `docker-entrypoint.sh` lines 14–19

---

### #51 · Coverage < 80% CI gate

**What:** Current coverage is 75% (8166 stmts, 2051 miss). CI enforces ≥ 80%. Largest gaps:
- `scheduler.py` 250 miss (62%)
- `nba_service.py` 258 miss (66%)
- `model_commands.py` 257 miss (59%)
- `market_recommender.py` 189 miss (68%)
- `nba_live.py` 126 miss (72%)

**Fix order (biggest return on fewest test lines):**

1. **`scheduler.py`** — Add tests for the 3 most common job functions (`refresh_player_stats`, `resolve_and_grade`, `drift_check`) by mocking the DB calls they make.
2. **`nba_service.py`** — Add tests for ESPN data-fetch helpers; mock `requests.get` to return fixture JSON.
3. **`nba_live.py`** — Add route integration tests for `GET /nba/today` and `GET /nba/props/<espn_id>` using Flask test client.
4. **`model_commands.py`** — Add CLI invocation tests using `runner.invoke()`.

**File:** `tests/test_services.py`, `tests/test_routes.py`

---

### #54 · Main branch has 7 test failures + 49 errors

**What:** Main branch test suite is not clean. These predate the #30 fix and are now resolved on `fix/30-days-rest-skew`.

**Fix:** Merge `fix/30-days-rest-skew` → `main`. Verify 776/776 pass before merging.

---

## Priority 2 — LOW (Grouped by Area)

### ML / Inference Quality

**#48 — `games_last_7_days` secondary train/inference skew**
- `app/services/ml_feature_builder.py`
- `compute_schedule_density()` uses `last_log_date + 1` as `current_game_date`, over-counting density when the real game is days away.
- Fix: pass the real `game_date` (available after #30 fix) into `compute_schedule_density()`. One-liner once Phase 1 is merged.

**#50 — Silent zero-fill at inference**
- `app/services/ml_model.py` line ~529
- Fix: `missing = set(feature_names) - set(features.keys()); if missing: logger.warning("Missing features at inference: %s", missing)`

**#31 — `_compute_z_score` log order dependency**
- `app/services/projection_engine.py`
- Fix: add `logs_sorted = sorted(logs, key=lambda x: x.game_date, reverse=True)` before slicing, or add an explicit assertion.

---

### Scheduler

**#38/#60 — `APP_TIMEZONE = "US/Eastern"` (deprecated alias + 5-module duplication)**
- Define `ET = ZoneInfo("America/New_York")` once in `app/utils/time_helpers.py`, import everywhere.
- Change `scheduler.py` string to `"America/New_York"` (IANA canonical).

**#39 — Simultaneous job firing (stats_refresh + injury_am at 10:00)**
- Stagger `injury_am` to `hour=10, minute=5` in `app/services/scheduler.py`.

**#42 — `resolve_and_grade` two-commit atomicity gap**
- `app/services/scheduler.py` lines ~806 and ~833
- Wrap all three operations in a single `db.session.commit()` at the end, using savepoints for per-item postmortem failures.

---

### Routes / API

**#25 — DB writes on GET requests**
- `app/routes/nba_live.py`
- Move snapshot writes to a dedicated `POST /nba/snapshot/refresh` endpoint called by the scheduler.

**#26/#27 — No bounds/type validation on placement endpoints**
- `prop_line` → float in `(-50, 100)`; `american_odds` → int in `(-5000, 5000)`; `prop_type` → `PropType.__members__`.

**#43 — Dual JS scripts on bet form**
- `app/templates/bets/form.html` lines 800–801
- Remove the `bet_builder.js` `<script>` include after testing the golden path with `unified_bet_builder.js` alone.

**#63 — Inline `requests.get` ESPN calls in `nba_live.py`**
- Lines 215, 496, 566 — extract `_get_game_summary()` and inline calls into `nba_service.py`.

**#58 — Healthcheck uses liveness `/health` instead of readiness `/ready`**
- `railway.toml` line 6 — change `healthcheckPath = "/health"` → `"/ready"`.

---

### Database / Models

**#14/#15/#16/#17 — Four missing indexes**
- Single Alembic migration revision:
  - `(user_id, created_at DESC)` on `Bet` (dashboard sort)
  - `external_game_id` on `Bet` (live grading lookup)
  - `cache_expires` on `PlayerGameLog` (prune job)
  - `job_name` on `JobLog` (observability queries)

**#9/#10 — Double query + Python-side aggregation in `bet_crud.py`**
- Replace Python iteration for `filter_stats` with a `SELECT COUNT(*), SUM(CASE ...)` aggregate query.

---

### Tests

**#52 (parlay push-leg)** — Add `test_grade_parlay_with_push_leg()` to `tests/test_parlay_redesign.py`.

**#53 (bonus multiplier)** — Assert stored payout value, not just element presence, in `test_parlay_redesign.py:403`.

**#51 (sparse history)** — Add test: call `project_stat()` with 9 game logs; assert return `{}`.

**#52 (absent ML artifact)** — Mock `load_active_model` → `(None, None)`; assert heuristic fallback fires, not 0.0 silent return.

---

### Documentation / `.env.example`

Four additions in a single pass:
- **#21** — `AUTO_DB_UPGRADE` with multi-worker race warning
- **#36** — `USE_ML_PROJECTIONS`
- **#59** — `MIGRATION_MAX_SECONDS`
- **#20** — `RATELIMIT_STORAGE_URI` (multi-worker note)

---

## Priority 3 — INFO (Low Urgency, High Leverage)

| # | Area | Fix |
|---|------|-----|
| 5 | CSP `unsafe-inline` | Document as known trade-off (`script-src` is safe) |
| 6 | HSTS header absent | Add `Strict-Transport-Security` in `add_security_headers()` for prod |
| 7 | OCR upload extension-only check | Add `content_type` assertion |
| 8 | `espn_id` format guard | Add alphanumeric regex guard before URL construction |
| 11 | N+1 on `display_label` | Ensure all render paths call `_attach_parlay_leg_counts` |
| 13 | `grade_bet` two-commit | Merge into single commit (same pattern as #42) |
| 22 | `inject_user()` on `/health` | Guard: skip CSRF form if `request.endpoint in health_endpoints` |
| 23 | `RATELIMIT_STORAGE_URI` at import time | Move inside `create_app()` factory |
| 28 | Inconsistent JSON schema | Standardize to `{"ok": bool, "error": str}` across all endpoints |
| 33 | `cv='prefit'` misleading comment | Correct to "calibration-set reuse risk" not "deprecated" |
| 35 | Stale defense data silent | Surface staleness in `/ready/model2` response body |
| 37 | Bias correction N threshold | Add comment: "revisit when N > 80" |
| 40 | Stale-job watchdog inline | Add dedicated 30-min watchdog cron job |
| 41 | Scheduler job count stale | Update CLAUDE.md to cite 17 jobs (not 9) |
| 44 | `setInterval` handle not stored | Store handle; `clearInterval` when all games final |
| 45 | Parlay queue not cleared on logout | Add submit listener on logout forms in `base.html` |
| 46 | `innerHTML` from server fragment | Prefer `replaceChildren()` for future safety |
| 61 | `_resolve_card_progress()` in routes | Extract to `nba_service.py` in next route refactor |
| 62 | Entrypoint executable bit | Verify: `git ls-files --stage docker-entrypoint.sh` → mode `100755` |

---

## Implementation Order Recommendation

```
Phase 1 — Merge & clean (1 PR)
  Merge fix/30-days-rest-skew → main
  Close #30, #47, #49 in REVIEW_PLAN.md
  Close #55, #56 as Disputed in REVIEW_PLAN.md

Phase 2 — Security & data integrity (1 PR)
  #24  outcome validation in manual_parlay()
  #26/#27  bounds + enum validation on placement endpoints
  #4   global rate limit fallback
  #20  document RATELIMIT_STORAGE_URI

Phase 3 — ML pipeline correctness (1 PR)
  #32  FEATURE_KEYS assertion
  #48  games_last_7_days real date (1-liner after Phase 1)
  #50  log warning on zero-fill
  #31  explicit sort before z-score slice

Phase 4 — DB indexes (1 Alembic migration)
  #14, #15, #16, #17 — four indexes in one revision

Phase 5 — Scheduler & atomicity (1 PR)
  #38/#60  timezone consolidation
  #39  stagger injury_am to 10:05
  #42  single-commit in resolve_and_grade
  #57  timeout-vs-error distinction in docker-entrypoint.sh

Phase 6 — Test coverage (1 PR, target ≥ 80%)
  #51  scheduler + nba_service + nba_live tests
  #53  replace db.session.execute mocks
  Parlay push-leg + bonus-multiplier + sparse-history + absent-artifact tests

Phase 7 — Housekeeping (1 PR)
  #43  remove legacy bet_builder.js
  #58  railway.toml healthcheck → /ready
  .env.example additions (#21, #36, #59)
  CLAUDE.md scheduler job count (#41)
  INFO items as bandwidth allows
```

---

*All findings verified by direct file reads. Statuses reflect ground-truth as of 2026-06-25.*
