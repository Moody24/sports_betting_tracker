# Plan C Increment 2 — Scenario Signal (design)

**Date:** 2026-07-17. **Status:** approved design, pre-plan.
**Depends on:** Plan B scenario engine (LIVE: 1,637,800 ScenarioSplit rows / ~580
players), Plan C Increment 1 + 1.5 (distributional heads LIVE, historical
training source).

## Goal

Cash in the Plan B engine for live scoring: compute `agreement_score` — the
signed, n-weighted share of a player's applicable conditional splits that sit
over/under the line — for every scored prop, surface it on the pick card, and
let a strong signal nudge `confidence_tier` by one step. The calibrated
distributional `P(over)` and the edge math stay untouched: this increment adds
an independent, explainable signal, not a second probability.

**Signal-first, deliberately:** scenario values do NOT enter `FEATURE_KEYS` or
model training in this increment. Feature integration is a later increment,
considered only after the live signal proves itself in-season.

## What exists today (verified)

- `ScenarioSplit(sport, player_id, player_name, stat, dim1, bucket1, dim2,
  bucket2, season_scope, n, raw_mean, shrunk_mean, baseline_mean)` — ESPN id
  namespace, but `player_name` is stored on every row.
- `agreement_score(player_id, stat, line, context) -> (score, n_matches)`
  already skips any dimension absent from `context` — partial context degrades
  matching, never correctness.
- `build_context` (historical/post-hoc) computes 10 dims. Live knowability:
  - **Fixed logic, knowable live:** `home_away`, `rest_bucket` (0/1/2/3+ from
    date gaps), `season_segment` (month bins), `fav_dog` (spread>7 = big; ties
    → 'fav').
  - **Data-dependent, knowable live via persisted state:** `total_bucket`
    (per-season quantile edges), `opp_def_tier` + `pace_tier` (as-of-date
    percentile ranks over team aggregates).
  - **Approximable:** `role` (starter/bench) from recent starter flags.
  - **Not live-knowable:** `game_script` (bucketed on realized final margin —
    excluded by construction), `teammate_context` (needs injury ingestion —
    out of scope).
- Live scoring identifies players by NAME (odds API / NBA namespace); the
  never-mix rule (espn-vs-nba-id-namespaces) forbids id unioning. The bridge
  is the name, resolved against data we own.

## Decisions (made during brainstorming, do not re-litigate)

1. **Integration depth: signal only.** No FEATURE_KEYS change, no retrain.
2. **Effect: display + bounded tier nudge.** Score + matched-split count in
   result fields and a context note. With `n_matches >= MIN_MATCHES` and
   `agreement <= -STRONG_THRESHOLD`: demote `confidence_tier` one step; with
   `agreement >= +STRONG_THRESHOLD`: promote `slight -> moderate` ONLY (a
   scenario signal never manufactures `strong`, and demotion is one step:
   `strong->moderate`, `moderate->slight`, `slight->no_edge`). Named constants
   with starting values `MIN_MATCHES = 5`, `STRONG_THRESHOLD = 0.5`,
   `MAX_PACK_AGE_DAYS = 7` — tunable, but these are the shipped defaults.
3. **Dimensions live: 7** — the six clean dims + approximated `role`
   (started >= 3 of last 5 store games → 'starter'). `game_script` and
   `teammate_context` never emitted.
4. **Crosswalk: in-memory resolver, no new table.** ~580 players; map built
   from `ScenarioSplit` distinct `(player_id, player_name)`.
5. **Bucket parity: persisted context pack**, written atomically with the
   splits by `refresh_splits`; fixed-logic bucketing extracted into shared
   functions used by BOTH the historical and live builders.
6. **Rollout: dark.** `USE_SCENARIO_SIGNAL` env flag, default false; flag-off
   is a regression-tested no-op.

## Components

### 1. `app/services/player_crosswalk.py`
`resolve_espn_id(player_name: str) -> str | None`.
- Map source: `ScenarioSplit` distinct `(player_id, player_name)`; built once
  per process (lru-cached); cache cleared by the refresh job.
- Normalization: NFKD accent strip, lowercase, strip punctuation, strip
  generational suffixes (jr, sr, ii, iii, iv).
- Collisions (two ids → one normalized name): drop both from the map at build
  time, warn once. Never guess.
- `OVERRIDES: dict[str, str]` for known odd spellings (starts empty).
- Unresolved → `None` (caller shows no signal).

### 2. Context pack (persistence)
- New table `ScenarioContextPack(id, sport, payload JSON, computed_at)` — one
  live row per sport, replaced by each refresh inside the SAME transaction as
  the splits (pack and splits cannot disagree).
- Payload: `{"total_edges": [lo, hi], "team_def_tier": {abbr: tier},
  "team_pace_tier": {abbr: tier}, "season": "2025-26"}` — current-season
  quantile edges for `total_bucket`, and per-team def/pace tiers ranked as-of
  the latest frame date. Small (~30 teams × 2 maps + 2 floats).
- Fixed-logic dims need nothing persisted: their bucketing functions
  (`fav_dog`, rest bins, season-segment months) are extracted in
  `scenario_dimensions.py` and called by both builders.
- One alembic migration. Local quirk: drive via `flask_migrate.upgrade()`
  from Python (CLI broken locally); back up `instance/app.db` first.

### 3. `app/services/live_context.py`
`build_live_context(espn_id: str, game: dict, *, as_of: date | None) -> dict`
- Inputs: slate game info (home/away, opponent abbr, spread, favored side,
  total — all already fetched for scoring), the pack, the historical store
  (player's last game dates for rest; last-5 starter flags for role), today's
  date (or `as_of` for replay).
- Emits ONLY populatable dims, with bucket labels byte-identical to what
  `refresh_splits` stored (enforced by the parity test). Missing pack → only
  fixed-logic dims. Team abbr normalization goes through the existing
  `espn_mapping.normalize_abbr`.

### 4. Integration — `ValueDetector.score_prop`
Flag-on, after existing scoring: resolve id → build context →
`agreement_score` → attach `scenario_agreement` (float), `scenario_matches`
(int), context note ("Scenario splits: 7 matches, lean over +0.62"), apply the
bounded tier nudge. Flag-off or any failure: fields absent/None, no note, no
nudge, results byte-identical to today.

## Error handling
- Whole scenario block wrapped; any exception → warn once per scan run, no
  signal, prop scores as today.
- No pack / stale pack (`computed_at` > `MAX_PACK_AGE_DAYS`, start 7): stale
  never nudges — degrade to fixed-dims-only matching (no pack) or no nudge
  (stale).
- Unresolved player or 0 matches: fields show no-signal, no nudge.
- Crosswalk collisions/overrides logged at map build — silent misattribution
  is structurally impossible.

## Testing
- **Parity replay test (keystone):** seeded historical store + odds fixture →
  run real `build_context`; for sampled (player, game) rows run
  `build_live_context(as_of=game_date)` and assert every emitted dim's bucket
  label equals the historical builder's label for that row. Guards the shared
  fixed logic AND the pack path (pack generated from the same fixture).
- Crosswalk: accents, suffixes, collision-drop, override, unresolved,
  cache-clear on refresh.
- Live builder: each dim's boundaries (rest 3+, role 3-of-5 flip, segment
  month edges, fav_dog at spread 0/±7); missing-pack degradation;
  `game_script`/`teammate_context` asserted ABSENT.
- Detector: flag-off byte-identical regression; flag-on mocked-agreement
  fields/note/nudge both directions; exact nudge table asserted; exception →
  clean fallback.
- Refresh: pack written atomically with splits; survives `--force`; staleness
  gate honored.
- Gates: full suite foreground, coverage >= 80%, ruff, bandit.

## Out of scope (recorded)
`teammate_context` (injury ingestion), live `game_script` (unknowable),
scenario features in `FEATURE_KEYS` (later increment), Plan D `line_move`
dimension, any UI redesign (the note rides existing context-note rendering).

## Deviations (recorded at implementation, 2026-07-17)

- Split execution: Codex implemented Tasks 1–4, Claude Tasks 5–9 (Codex hit
  its usage limit — harness budget-first handoff).
- `season_segment_label` returns None for out-of-window months INCLUDING
  September (Codex fix `ec215e2`; the plan's sketch would have mislabeled
  Sep as 'early').
- Pack refresh made warning-free on the skip path (Codex, `52c3c20`).
- `tests/test_nba_service.py` was CREATED (plan said "append" but no such
  file existed).
- Live-context test fixture date arithmetic corrected (`(n-1-i)*2`, plan had
  an off-by-one making the newest seeded game 2 days older than intended).
- `_empty_score` also carries `scenario_agreement`/`scenario_matches` (None)
  so every score dict has a uniform shape (not in plan; additive).
- Three pre-existing tests MOCK `fetch_odds_combined` with 2-tuples; the
  widened 3-tuple return broke them in the full suite only (the plan's
  "update every caller" step found real calls but not mock configurations).
  Fixed to 3-tuples. Process lesson: the first gate run masked the failure
  behind a pipe (`| tail` eats the exit code) — gates must run with
  `set -o pipefail`.

## Post-merge runbook
1) Migration (python `flask_migrate.upgrade()`, after DB backup).
2) `flask refresh-splits --force` → first pack materialized.
3) Replay spot-check: build live context for a recent historical date for a
   few known players; compare `agreement_score` output to direct SQL.
4) Flip `USE_SCENARIO_SIGNAL=true` when satisfied (off-season: no visible
   change until October).
