# Basketball-first sport expansion roadmap

Checked: **2026-10-07**. Status: **planning backlog; NFL/NHL implementation and UFC exploration are not launched**.

This audit supplements the [canonical system contract](../architecture/system-contract.md); it does not replace its schemas or grant source access. Scope is basketball verification first, then phased NFL and NHL support, with UFC exploratory. No new accounts, subscriptions, provider ingestion, real model training, or model activation were performed for this roadmap. Passing software tests establishes implementation behavior, not model quality or profitability.

## 1. Current foundation and limits

| Area | Evidence in this checkout | Expansion implication |
|---|---|---|
| Sport adapters | [SportService and registry](../../app/services/base.py) define scoreboard, boxscore, odds, props, merged games, and grading; NBA is the registered implementation. | A score-only sport must not imply that all abstract capabilities exist. Add explicit capability availability before exposing navigation or actions. |
| Historical storage | [HistoricalGameLog and ScenarioSplit](../../app/models.py) have a sport discriminator; historical rows are unique by sport/player/game. [Stat catalogs](../../app/services/sport_config.py) include NFL, but not NHL or UFC. | NFL's catalog is a starting inventory, not a functioning NFL pipeline. Preserve source identity and distinguish absent, zero, inactive, and did-not-play values. |
| Transaction and identity boundaries | [Bet, PickContext, OddsSnapshot, PlayerIdentity](../../app/models.py) lack a general sport/provider identity boundary; PlayerIdentity specifically maps ESPN to NBA IDs. | Add sport-scoped records and migrations before multi-sport recording, joins, grading, or training. A numeric provider ID alone is unsafe across sports. |
| Model inputs | [Feature builder](../../app/services/ml_feature_builder.py) contains basketball minutes, shooting, usage and defensive features. [Historical adapter](../../app/services/historical_training_source.py) converts JSON into NBA PlayerGameLog-shaped rows. | Reuse evaluation and persistence patterns, not NBA feature meanings. Passing `sport='nfl'` to the history loader does not make its feature adapter an NFL model. |
| Model identity | [Model 1](../../app/services/ml_model.py), [distributional models](../../app/services/distributional_model.py), and [Model 2](../../app/services/pick_quality_model.py) use NBA-oriented model names, defaults and feature sets. | Namespace artifacts, active-model queries, calibrators and evaluations by sport, target, horizon and feature version before a second sport can train. |
| Shared feed | [Basketball collector](../../app/services/basketball_feed.py) owns the NBA feed key, source normalization, retry state and durable revisions; [SSE hub](../../app/services/feed_stream.py) reads shared DB state. | Reuse the bounded collector/fanout design only after defining each sport's lifecycle and quota. Keep provider delay distinct from internal delivery time. No browser-triggered upstream fetches. |

## 2. ML readiness assessment

**Useful safeguards already exist.** Model 1 builds each target from prior player logs, derives historical defense from preceding games, and sets unverified historical game totals to zero. Its activation/loading gate requires the current context version, a game-date split, at least 100 validation rows, an improvement over the recent-five-game baseline and the stat-specific MAE limit. Its final early-stopping partition is inside the training period. See [_build_training_rows, _projection_validation_indices and _projection_qualifies](../../app/services/ml_model.py).

Model 2 requires dated examples and separate fit/early-stop/calibration/test partitions, class coverage, and minimum calibration/test counts. It excludes bootstrap records by default, rejects polluted matchup context, and fails if calibration fails. Paper cohorts are included by default and must be reported separately from user selections. See [_resolved_training_rows and _prepare_training_data](../../app/services/pick_quality_model.py).

The [rolling evaluation](../../app/services/rolling_backtest.py) filters quotes captured before event start, separates decision and closing observations, fits prior-date folds, and reports coverage, probability quality, segment results and uncertainty. Its `promotion_verdict` can return `SHADOW`, `HOLD`, or `PROMOTE_CANDIDATE`; those are evaluation decisions, not guarantees of future returns.

**Two concrete basketball issues found during this review were corrected in the same work:**

- Model 2's stored probability bias previously used held-out test labels. It now derives bias only from calibration predictions/labels, freezes the shrink setting, and measures final test metrics after the same postprocessing used at inference. Metadata records `postprocessing_version=calibration_only_v1` and `calibration_bias_source=calibration_partition`. Unversioned legacy bias is ignored; older artifacts still require requalification.
- Calibrated artifact persistence previously fell back to saving an uncalibrated base model. Persistence failure now preserves the incumbent and returns an error; distinct filenames avoid overwriting its artifact. The [new regression tests](../../tests/test_pick_quality_postprocessing.py) use mocked estimators and writers. Changing test labels cannot change the stored bias or calibrator fit inputs. No model was retrained or promoted.

**Remaining acceptance work before expanding ML:**

| Backlog | Concrete gap | Acceptance evidence |
|---|---|---|
| ML-01: grouped temporal partitions | Model 1 diagnostic `TimeSeriesSplit`, Model 2 early-stop tails, and distributional/rolling early-stop tails split row counts. Related players/markets from one event can cross a boundary. | Split by event/date groups; NFL evaluation additionally groups week/season. Assert no event or duplicate market crosses fit/early/calibration/test. Reject missing decision timestamps for evaluation. Source: [Model 1](../../app/services/ml_model.py), [Model 2](../../app/services/pick_quality_model.py), [distributional splitters](../../app/services/distributional_model.py), [rolling splitters](../../app/services/rolling_backtest.py). |
| ML-02: promotion and artifact bundle | Model 2 activates a successfully trained/calibrated artifact without consulting the rolling verdict. Distributional training commits its model active before completing calibrator persistence. | One explicit promotion gate and compatible model/calibrator/feature manifest; no partial activation; failed candidates stay inactive; incumbent rollback test. Treat index-fallback splits as diagnostic only. |
| ML-03: point-in-time provenance | Existing history has fetch times, but a fetch time does not establish when an injury, lineup, opponent feature or corrected stat was knowable. | Store source publication/observation and internal capture times separately. Rebuild each feature as of the declared decision cutoff. Perturb future labels, corrected results and closing quotes and prove earlier features/predictions do not change. |
| ML-04: comparable final metrics | Historical artifacts may predate the revised postprocessing or have different sample/cohort coverage. | Re-evaluate a frozen full inference pipeline on untouched later periods; version metrics and feature manifests. Report game counts, independent cohorts, missingness, calibration/Brier/log loss, target-specific error and interval coverage against simple baselines. |
| ML-05: shadow monitoring | Basketball thresholds and minimum row counts do not establish adequacy for another sport. | Predeclare sport/market sample requirements and uncertainty limits before looking at final results. Group resampling by event, examine seasons/roles/market segments, keep a later prospective shadow period, and fall back when data or model gates fail. |

Use [existing ML validation tests](../../tests/test_ml_validation_program.py) as reusable test patterns. Test coverage is not evidence that a current database contains enough authorized, representative history.

## 3. Source and cost prerequisites

Checked against provider documentation on the date above; recheck before implementation or access changes.

| Sport | Verified candidate and limitation | Gate |
|---|---|---|
| NBA | BALLDONTLIE's free tier includes Games with a five-request/minute limit; player game stats and odds are outside that free tier. [Official NBA tier table](https://docs.balldontlie.io/#account-tiers). | Finish basketball operations verification with a securely configured, authorized key. Game scores alone cannot supply player-prop training history or live odds. |
| NFL | BALLDONTLIE lists Teams, Players and Games on its free tier at five requests/minute. Stats, injuries, team stats, odds and player props need higher tiers. [Official NFL tier table](https://nfl.balldontlie.io/#account-tiers). | A free schedule/score pilot is a candidate after basketball verification. Player-prop ML remains blocked until separately authorized historical and decision-time inputs exist. No paid tier is assumed. |
| NHL | BALLDONTLIE's free tier includes Teams and Players; **Games and Box Scores are not free**. [Official NHL tier table](https://nhl.balldontlie.io/#account-tiers). | Free NBA access cannot be reused as an NHL Games entitlement. Identify a permitted free source or authorized local dataset before implementation; otherwise keep NHL blocked. |
| UFC | No permitted, sufficiently complete free UFC schedule/stat/odds source was verified by this audit. | Research-only source matrix and schema fixtures. Public website availability, a scraping package, or another sport's API key does not establish permission or coverage. |

BALLDONTLIE permits caching, analysis, ML and downstream products using lawfully obtained data, subject to its restrictions, including competing services and resale of unmodified data. These permissions do not expand endpoint entitlement or quotas. It makes no guaranteed live-latency claim and requires independent verification for grading/settlement reliance. [Terms, §§5–9](https://www.balldontlie.io/terms). Reuse the [source-admission review](2026-10-07-data-source-review.md) for ESPN/NBA restrictions; do not treat code availability or the free score feed as permission to enable those sources.

Every sport's admission record must identify permitted endpoints, historical storage and ML use, downstream display rights, retention, source corrections, quota scope and a safe secret configuration. Quotas shared across sports/keys must be confirmed before assigning poll intervals. No trial, purchase, account creation or credential sharing is part of this backlog.

## 4. Phases and acceptance gates

| Phase / proposed owner | Deliverable | Must pass before advancing |
|---|---|---|
| **0 — Basketball verification** / backend + QA | Validate the existing collector, replay, manual recording and source labels on the supported runtime. | Migrations and restart replay verified; malformed/partial pages, no-key state, outage/backoff, ET rollover and slow-client bounds pass. Authorized live observations establish actual capture completeness and measured latency; metadata never claims source time that was not supplied. No external actions or paid feeds are needed to run fixture checks. |
| **1 — Basketball ML qualification** / ML + data | Complete ML-01–05, including the corrected Model 2 path. Keep legacy artifacts distinguishable from qualified candidates. | Source-admitted history, reproducible dataset/feature hashes and split manifests, immutable test horizon, calibrated final-pipeline metrics, baseline comparison and prospective shadow evidence. Activation is a separate reviewed decision; this roadmap does not authorize training or promotion. |
| **2 — Shared sport boundary** / backend + database | Design sport/provider event and entity identity; add sport to bet/quote/context/evaluation paths; define capability availability and sport-specific settlement rules. | Migration defaults preserve NBA history; fixtures with identical numeric IDs across providers/sports cannot collide; NBA grading/training cannot read NFL/NHL rows; caches and artifact names include sport. Update SSOT before implementing contracts. |
| **3A — NFL schedule/score pilot** / ingestion + frontend | Add an authorized Games adapter and bounded shared feed; support season phase/week, venue time, clock/period and non-final states. | Admission and Phase 0/2 pass; complete game fixture coverage, correction/reconnect tests, quota budget and source labels. Expose only capabilities that work. No props, predictions or automated grading implied by a scoreboard. |
| **3B — NFL historical/model shadow** / data + ML | Start with one clearly scoped target and baseline; add role/opportunity and team/opponent histories with decision-time provenance. | Authorized player/team history and required context exist; weekly/season holdouts and event-grouped uncertainty pass; inactive/DNP handling is explicit. Only add price-based evaluation when real timestamped quotes are available. |
| **4A — NHL source and score pilot** / data + backend | Resolve the free-source blocker, then implement sport-specific lifecycle and score fixtures. | Written endpoint entitlement, sufficient schedule/final coverage and Phase 2 isolation. Test regulation/overtime/shootout representation and postponement/correction behavior before exposing a score page. |
| **4B — NHL historical/model shadow** / ML + data | Separate skater and goalie target families, with role/time-on-ice and lineup context. | Permitted granular history, verified participation and goalie availability at decision time, season/date holdouts, market-specific evaluation and bounded shadow operation. No NBA model weights or thresholds reused as qualified NHL output. |
| **5 — UFC exploratory only** / research + data | Write a source matrix, bout/card schema and small authorized offline fixture set. | Confirm sufficient rights and history before any adapter work. Explicitly model fighter identity, bout changes, scheduled rounds, result/method, draws/no-contests and corrections. A feasibility review decides whether development is warranted. No UFC tab or model launch is scheduled. |

## 5. Sport-specific modeling backlog

These are hypotheses to test, not established predictive advantages.

| Sport | Feature and schema work | Evaluation/product work |
|---|---|---|
| NFL | Distinguish passer, rusher and receiver opportunities: attempts, targets, snap participation and role changes. Preserve season/week/phase, rest, venue/weather observation time, opponent strength and availability. Replace NBA minutes/usage/shooting features. | Evaluate seasons and weeks in order; split all players/markets from a game together. Separate yardage/count targets, participation uncertainty and team result models. Support pushes, voids, reschedules and source corrections with sport/book-specific rules before grading. |
| NHL | Define skater shots/goals/assists and goalie saves/goals-against separately. Track time-on-ice, line/power-play role, starting goalie confidence, rest and opponent context as known then. Add an NHL stat catalog and null semantics. | Test count/distribution baselines and interval coverage by role, not just aggregate MAE. Specify overtime/shootout inclusion per target and market. Unknown starters or incomplete participation require abstention. |
| UFC | Use fighter/bout/card entities rather than team home/away as the core schema. Candidate context includes weight class, scheduled rounds, opponent history and timing of opponent/weight changes. Keep identifiers independent of names. | Split complete event cards chronologically; avoid post-bout rankings, results or corrections entering pre-bout features. Assess sparse histories, changing opponents, draws/no-contests and result-method targets before selecting a model family. |

## 6. Ready-to-pick-up backlog order

1. **BB-OPS:** finish Phase 0 evidence and record remaining operational limitations.
2. **BB-ML:** implement ML-01–05; requalify artifacts without claiming that past results imply future performance.
3. **SPORT-ID:** design and test Phase 2 migrations and capability contracts using offline fixtures.
4. **NFL-SCORES:** proceed only when the first three gates and free endpoint admission pass; keep NFL ML as a separate blocked ticket until historical inputs are admitted.
5. **NHL-DATA:** investigate permitted free coverage; do not build a UI around an unentitled endpoint.
6. **UFC-FEASIBILITY:** research rights, history quality and bout semantics, then make an explicit go/no-go decision.

The release checklist must mark source, schema, model and product readiness independently. A green score feed does not imply working player props, calibrated probabilities, or a supported settlement path.
