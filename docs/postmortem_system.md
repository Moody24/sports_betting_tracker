# Bet Postmortem Intelligence System

## What It Does

After each player-prop bet settles, the postmortem system automatically:

1. Loads the pregame analysis snapshot (`PickContext`) — what the model expected
2. Loads the actual game result from `PlayerGameLog` — what actually happened
3. Computes diagnostic deltas (minutes, attempts, efficiency, pace)
4. Assigns **structured reason codes** via a deterministic rules engine
5. Persists a `BetPostmortem` record (idempotent — safe to re-run)
6. Displays the diagnosis in the bet history UI as a collapsible panel

### Example Output

> **Benedict Mathurin — Under 1.5 3PM — LOSS**
>
> Primary reason: **Volume Spike** (High confidence)
> Secondary reason: Minutes Increase
>
> | | Projected | Actual |
> |---|---|---|
> | 3PM | 1.2 | 2.0 |
> | Minutes | 24 | 31 |
> | 3PA | 3.8 | 6.0 |
>
> Projection error: +0.8

---

## Architecture

```
pregame features (PlayerGameLog, TeamDefense, InjuryReport)
    ↓
ProjectionEngine  →  projected_stat
    ↓
ValueDetector     →  projected_edge, confidence_tier
    ↓
PickContext (stored at bet placement)
    ↓
─── bet settles ───
    ↓
PostmortemService
    ├── load PickContext (pregame expectation)
    ├── load PlayerGameLog[game_date] (actual stats)
    ├── load PlayerGameLog[history] (10-game baseline)
    ├── load GameSnapshot (OT / blowout detection)
    ├── compute deltas
    ├── _assign_reasons() deterministic rules engine
    └── BetPostmortem.upsert()
    ↓
Model 2 training (new features: minutes_volatility, stat_attempts_volatility)
```

---

## Key Files

| File | Role |
|---|---|
| `app/enums.py` | `PostmortemReason` enum — all valid reason codes |
| `app/models.py` | `BetPostmortem` SQLAlchemy model |
| `app/services/postmortem_service.py` | Diagnosis engine (`create_or_update_postmortem`, `_assign_reasons`) |
| `app/routes/bet.py` | Wired into `nba_update_results` and `grade_bet` |
| `app/services/scheduler.py` | Wired into `resolve_and_grade` (automated nightly grading) |
| `app/services/feature_engine.py` | Adds `minutes_volatility`, `stat_attempts_volatility` to context |
| `app/services/pick_quality_model.py` | Includes new volatility features in Model 2 training |
| `app/templates/bets/list.html` | Postmortem panel in the bet card (collapsible `<details>`) |
| `app/cli.py` | `flask backfill-postmortems`, `flask postmortem-report` |
| `migrations/versions/a1b2c3d4e5f6_add_bet_postmortem.py` | Schema migration |
| `tests/test_postmortem.py` | Unit + integration tests |

---

## How Reason Codes Are Assigned

The `_assign_reasons()` function uses deterministic business rules — no ML involved in the diagnosis itself. Each rule scores a confidence between 0 and 1. The top-3 scoring codes become `primary_reason_code`, `secondary_reason_code`, `tertiary_reason_code`.

### Rules (in priority order)

| Condition | Reason Code | Typical Confidence |
|---|---|---|
| Game total > 230 pts (OT heuristic) | `ot_variance` | 0.80–0.85 |
| Score diff > 22 pts | `blowout_distortion` | 0.75–0.80 |
| `|minutes_delta| ≥ 8` | `minutes_miss` | 0.65–0.90 |
| Large minutes delta AND pregame trend was `stable` | `role_change` | 0.72 |
| Attempts pct swing ≥ +35% | `volume_spike` | 0.65–0.88 |
| Attempts pct swing ≤ -35% | `volume_drop` | 0.65–0.88 |
| Rate (stat/attempt) delta > +15% | `efficiency_spike` | 0.68 |
| Rate (stat/attempt) delta < -15% | `efficiency_drop` | 0.68 |
| `projected_edge < 0` | `insufficient_edge` | 0.65 |
| `0 < projected_edge < 5%` | `line_value_miss` | 0.58 |
| `|projection_error| > 2σ`, no other driver | `projection_model_miss` | 0.55–0.80 |
| `|projection_error| > player_variance` AND `variance ≥ 4` | `high_variance_event` | 0.62 |
| `|projection_error| ≤ 1σ` AND `|miss_margin| ≤ 1.5` | `normal_variance` | 0.75 |
| No evidence found | `unknown` | 0.40 |

### What Data Is Required

The service **gracefully degrades** when data is missing:
- **No `PickContext`**: `projected_stat` and `projected_edge` will be `None`; projection-based rules are skipped
- **No `PlayerGameLog` for game date**: minutes/attempts deltas are `None`; those rules are skipped
- **No `GameSnapshot`**: OT/blowout flags default to `False`
- **Insufficient history** (< 5 games): expected baselines are `None`

A postmortem is still created in all these cases — it just uses whatever evidence is available.

---

## ML Training Integration

### New Features Added to Model 2 (Pick Quality Classifier)

Two new volatility features are now computed at **bet placement time** and stored in `PickContext.context_json`:

| Feature | Description |
|---|---|
| `minutes_volatility` | Std dev of player's minutes over last 20 games |
| `stat_attempts_volatility` | Std dev of FGA (for points) or FG3A (for threes) over last 20 games |

These features allow Model 2 to learn that **high-variance players are riskier** regardless of edge direction.

**Backward compatibility**: Older `PickContext` rows will have `0.0` for these fields. XGBoost treats absent/zero signals as neutral, so the model degrades gracefully to the existing features for old data.

### Planned Phase 2 (not yet implemented)

A separate **failure-mode classifier** can be trained using `BetPostmortem` records once sufficient data accumulates (target: 200+ postmortems):

- Input: pregame features from PickContext
- Output: probability distribution over failure mode categories
  - `structural_miss` (minutes/volume/role)
  - `projection_model_miss`
  - `variance_loss` (normal variance / high variance event)
  - `market_miss` (line value / edge quality)

This would let the system flag props like: *"This type of bet has historically had a 40% chance of a volume-spike miss"*.

---

## Usage

### Automatic (Production)

Postmortems are created automatically:
- By `resolve_and_grade()` (scheduler, nightly at 1 AM ET)
- By `nba_update_results()` (user clicks "Check Now")
- By `grade_bet()` (manual grading dropdown)

### Backfill Historical Bets

```bash
# Analyse settled bets from last 60 days
flask backfill-postmortems --days 60

# Dry-run first to see what would happen
flask backfill-postmortems --days 90 --dry-run

# Re-analyse bets that already have postmortems
flask backfill-postmortems --days 30 --overwrite
```

### View Aggregate Report

```bash
# What reason codes are most common in the last 30 days?
flask postmortem-report --days 30

# Include reasons with fewer occurrences
flask postmortem-report --days 60 --min-count 1
```

### Database Migration

```bash
flask db upgrade
```

---

## Known Limitations

1. **OT detection is a heuristic**: total score > 230 pts is used as a proxy.
   A definitive OT flag would require parsing period-level score data from ESPN.

2. **Actual pace is not computed**: we have expected pace (from `opp_pace` in the context) but no reliable actual game pace without additional API calls. The `pace_miss` reason code exists but is not yet populated.

3. **Teammate availability shifts**: the `teammate_availability_shift` and `injury_context_miss` codes exist but are not automatically assigned by the rules engine yet. They require comparing pre-game vs post-game rosters, which is not yet available.

4. **Combo props (PRA)**: the minutes/attempts analysis works best for single-stat props. For `player_points_rebounds_assists`, the attempts analysis is skipped (no single "attempts" field covers all three components).

5. **PlayerGameLog freshness**: postmortems created before the scheduler has refreshed completed game logs may lack `actual_minutes` and `actual_attempts` data. Re-running `create_or_update_postmortem` after logs are refreshed will update the record.

6. **No line-movement analysis yet**: the `OddsSnapshot` table exists with line-movement data, but it is not yet integrated into the postmortem. The `market_moved_against_us` code is available but not triggered automatically.
