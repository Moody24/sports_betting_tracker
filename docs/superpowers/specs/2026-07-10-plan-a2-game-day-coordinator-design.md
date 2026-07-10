# Plan A2: Game-Day Coordinator — Tiered Polling & Event Chains (Design)

Date: 2026-07-10
Status: implemented (this plan)
Predecessor: Plan A (data platform foundations) — merged to main @ 8472d59,
HistoricalGameLog backfilled with 79,603 rows (3 seasons, ESPN id namespace).

## Goal

Replace fixed-time scheduling with game-aware scheduling: tight polling only
while NBA games are live, event chains that fire grade → postmortem →
snapshot-finalize → history-append minutes after each game ends, no re-fetch
of already-persisted data, and a HistoricalGameLog that stays current once
the 2026-27 season starts.

## Constraints (environment truths)

- App runs locally on the user's Mac; the scheduler is NOT always-on. The
  machine sleeps; the app may be closed for days. Every mechanism must be
  state-driven with catch-up semantics, not moment-driven.
- stats.nba.com is unusable from this machine (silently drops requests).
  All game data comes from ESPN (scoreboard + summary APIs, free, no key)
  and the sportsdataverse hoopR parquet dumps — the same sources that built
  the 79,603-row backfill.
- HistoricalGameLog ids are the ESPN namespace (athlete_id / ESPN game_id).
  All appended rows MUST stay in that namespace. Never run the legacy
  `backfill-logs` (stats.nba.com / NBA ids) over seasons this system covers.
- Odds API calls cost quota and remain guarded by APIBudgetManager (Plan A).
  ESPN calls are free but still minimized on principle (no-refetch guard).
- All date/window logic in ET (`ZoneInfo("America/New_York")`), matching the
  rest of the app.

## Architecture

One new module `app/services/game_day_coordinator.py`, one new APScheduler
job (`game_day_coordinator`, IntervalTrigger every 5 minutes), plus small
guards on existing jobs. No new tables, no new dependencies. The DB itself
is the event log: Bet outcomes, GameSnapshot.is_final, and
HistoricalGameLog row presence ARE the state machine.

### Tick state machine (per 5-min tick)

1. DORMANT — one free ESPN scoreboard call per ET day answers "any games
   today?". If none (off-season): cache the verdict for the ET day; all
   further ticks that day exit instantly with zero network. In-memory cache
   only; a restart re-checks once (cheap, correct).
2. PRE-GAME — games exist, first tip > 30 min away: exit cheap (no
   scoreboard re-fetch beyond the daily one) until the window opens.
3. LIVE — first tip − 30 min until all games final: fetch scoreboard each
   tick; detect status transitions.
4. POST — all of today's games final AND all chains complete: return to
   DORMANT until the next ET day.

### Newly-final detection (state diff, not events)

A game needs the chain if the scoreboard says FINAL but the DB disagrees on
any of: (a) pending bets exist for that game, (b) its GameSnapshot is not
is_final, (c) HistoricalGameLog has no rows for its game_id. This makes the
first tick after any downtime automatically heal everything missed.

### Event chain (per newly-final game, in order, idempotent)

1. Grade that game's pending bets (reuse resolve_pending_bets pathway).
2. Generate postmortems for newly settled legs (existing
   create_or_update_postmortem, savepoint-per-bet as today).
3. Finalize the GameSnapshot (is_final, home/away scores).
4. Append player rows to HistoricalGameLog from the ESPN summary API
   (one call per game): map via the established conventions —
   ESPN athlete_id/game_id as strings, ESPN→NBA team-abbr normalization,
   NBA-30 validation (drops exhibitions), starter flag direct, usage_pct
   computed from team totals (chances × teamMin/5 ÷ (min × team chances)),
   same stats-payload keys as import-hoopr-logs.
5. Write a JobLog row (job_name='game-final-chain', message = game id +
   step outcomes). Failures in one step don't block other games; the next
   tick retries remaining diffs.

### Catch-up depth

- Each LIVE/daily pass also scans the previous 3 ET dates (ESPN scoreboard
  date param) for final games missing HistoricalGameLog coverage and runs
  step 4 for them.
- Weekly reconciliation job (Sunday morning): `import-hoopr-logs` over the
  current season only — already idempotent — closes any deeper holes.

### No-refetch pillar

- Step 4 checks HistoricalGameLog for existing game_id rows BEFORE calling
  the ESPN summary endpoint; skip the call entirely if present.
- This existing-rows-check-before-fetch pattern is the documented convention
  for all future per-game fetchers (MLB/NFL in Phases 3/4).

### Caching policy

Three deliberate layers — and one hard rule:

- Day-verdict memo: the DORMANT tier caches the "any games today?" answer
  in memory for the current ET day (restart re-checks once; cheap).
- Persistence as cache: for final games, HistoricalGameLog rows, graded
  Bet outcomes, and is_final snapshots ARE the permanent cache — the
  no-refetch guard makes every per-game fetch happen at most once, ever.
- Existing TTL caches reused where compatible: context_service's 24h
  past-date scoreboard cache makes the 3-day lookback nearly free;
  nba_service's 60s scoreboard cache is fine under a 5-minute tick.
- HARD RULE: the LIVE-tier scoreboard read must never be served by a cache
  whose TTL ≥ the tick interval (context_service's 10-min today-cache is
  the known offender) — otherwise finals detection lags a tick. The
  coordinator uses the fresh/60s-TTL path for live status.

### Changes to existing jobs

- `resolve_and_grade` (01:00 ET) and `_update_final_snapshots` (23:15 ET):
  KEPT as thin safety nets — they are idempotent and cover coordinator bugs.
- `snapshot_props_odds` (every 2h, 8am–10pm): gains a games-today guard —
  skip entirely on empty days and through the off-season (Odds API quota
  saving; biggest immediate payback).
- All other jobs unchanged in A2 (YAGNI; off-season dormancy for stats
  refreshers can ride a later plan if wanted).

## Testing

Mocked ESPN scoreboard/summary fixtures (unittest, foreground):
- tier transitions incl. dormant-day caching and ET day rollover
- newly-final detection from each DB-disagreement condition independently
- full chain execution and ordering; JobLog row content
- idempotency: re-tick after completed chain does nothing, mid-chain crash
  resumes remaining steps
- catch-up: simulated multi-day downtime heals bets/snapshots/history
- no-refetch guard skips the summary call when rows exist
- odds-job guard: skips on no-game days, runs on game days
- usage_pct/starter mapping parity with the hoopR import conventions
Full suite + ruff + bandit before every commit, per repo standards.

## Non-goals (A2)

- No ScenarioEngine/ScenarioSplit refresh in the chain (Plan B will append
  itself as a chain step when it exists).
- No MLB/NFL ingestion; the coordinator is NBA-only but sport-parameterized
  where free.
- No UI changes; no new tables; no changes to model training.
- No dynamic APScheduler self-rescheduling (rejected Approach B) — the
  5-minute interval + cheap exits achieve the same effect with less state.

## Rollout

Implemented on a feature branch via subagent-driven development (per-task
review), merged to local main after final review + full suite, per the
Plan A workflow. Off-season note: the coordinator will sit DORMANT until
October 2026; correctness is provable only via the test suite now, so test
coverage is the acceptance bar. A manual `flask coordinator-tick` CLI
command is included for on-demand verification once games resume.
