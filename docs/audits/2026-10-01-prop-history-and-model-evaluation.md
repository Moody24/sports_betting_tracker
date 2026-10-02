# NBA prop history and model evaluation — 2026-10-01

| Severity | Finding | Effect |
|---|---|---|
| High | Historical defense and closing game totals entered model training without a decision-time cutoff. | Old model scores cannot justify live promotion. |
| High | No verified historical player-prop quotes are stored locally. | Bet profitability, calibration to offered lines, and closing-line value remain untested. |
| Medium | Team box scores show allowances and pace, not defensive scheme, assignment, or coverage shape. | Matchup patterns remain analysis context; scheme claims and automatic-pick features require separately observed tracking data. |

The local `prod-readiness` check on October 1 reported **NOT READY FOR
PRODUCTION**: 0 resolved pick-quality examples with context versus its 300-row
gate, no captured `OddsSnapshot` rows, no operational `PlayerGameLog` rows,
and a prior-season defense profile observed through April 12 (172 days old).
All six legacy point artifacts and four legacy quantile heads are held by the
new inference guard; local `USE_ML_PROJECTIONS` is also disabled. There were no
NBA games on that day's slate; these are preseason readiness findings, not
evidence of a missed live game.

## Historical offered-line search

The local database still has no historical NBA player-prop `OddsSnapshot`
rows. No source below was imported merely because it contains a numeric line.
The evaluation contract needs event and player identity, bookmaker, market,
both offered prices where available, a pre-tip observation timestamp, and
licensed use. A closing line is not interchangeable with a T-60 decision quote.

| Source | Verified coverage | Decision |
|---|---|---|
| [Owls Insight historical props](https://www.owlsinsight.com/docs) | Provider documents NBA backfilled opening/closing props from 2022–23 across DraftKings, Caesars, BetMGM and ESPN BET, plus a separate timestamp-filtered snapshot endpoint. Some older rows have only one unlabelled price; even two-sided "closing" quotes can predate tip by hours. Account and MVP-tier access are required, and actual NBA date/book coverage has not been sampled with a key. | Highest-priority pilot alongside The Odds API: inspect `/history/coverage`, then a few 2024–26 games and T-60 snapshots. Reject ambiguous one-way prices and stale closes. [Terms](https://www.owlsinsight.com/terms) prohibit redistribution of API data without written consent, so beta display rights need separate review. |
| [The Odds API historical event odds](https://the-odds-api.com/liveapi/guides/v4/index.html#historical-event-odds) | Player-prop snapshots from May 2023, at five-minute intervals; paid plans only. The configured key returned HTTP 401 `HISTORICAL_UNAVAILABLE_ON_FREE_USAGE_PLAN` for a January 2025 events request, charged 0 credits, and reported 14 remaining. | Best documented route for an event-level, book-specific pilot once licensed access is available. The provider quotes 10 usage credits per returned market per region per event snapshot. |
| [Statyx historical props](https://api.statyx.io/docs.html) | Documents per-book opening and closing NBA props linked to its game/player IDs. The public documentation does not establish the earliest NBA prop date or decision-time snapshots. | Secondary pilot after asking for a dated sample and access terms; closing prices alone cannot test T-60 decisions. |
| [PropLine resolved-prop backfill](https://prop-line.com/historical-backfill) | Offers graded per-book closing props and raw tick exports, but the archive starts April 2026. Published price is $99 for a seven-day export window. | Possible limited recent-period pilot, not a replacement for 2023–25 history. Verify NBA coverage and quote timestamps before purchase. |
| [The Odds Gap props export](https://theoddsgap.com/data) | Four checkpoints per prop, with timestamps and a best price, but no full per-book ladder. Its archive begins August 2026. Its public markets endpoint showed no `basketball_nba` rows on October 1. | Useful prospective research option once NBA markets appear; cannot fill the 2023–26 backtest gap. Its terms allow research with attribution and restrict repackaging or commercial reuse. |
| [NBA Prop Finder reports](https://github.com/ANickFlower/nba-props-app/tree/main/reports) | Eight dated report pairs in January–February 2026, ten selected props in the inspected January 28 CSV. Lines, book and price appear, but an exact capture time does not. | Selection-biased research sample, not a complete decision-time price history. Do not import as authoritative quotes. |
| [Public historical_prop_data.csv](https://github.com/DhirenShivdasani/NBA-player-prop-analysis/blob/main/historical_prop_data.csv) | 1,390 rows for January 31, 2024 with name, team, value, prop and date. No book, offered price, event ID or capture time. | Insufficient for the quote contract. |
| [NBA Historical Stats and Betting Data](https://www.kaggle.com/datasets/ehallmar/nba-historical-stats-and-betting-data) | Game moneyline, spread and total files, with no player-prop file. | Useful game context, not prop history. |

The existing `flask import-player-prop-odds` command is the ingestion path for
licensed CSV/JSON data. A small, costed pilot should verify identity joins,
book/market coverage and T-60/T-10 timestamps before a season-scale purchase.
Owls Insight and The Odds API are the two candidates with documented paths to
timestamped quotes; neither has been validated with historical NBA sample rows
under the available credentials.
Prospective scheduler capture remains necessary for current-season evaluation.

## Point-model integrity and challenger

Before this change, every one of 71,979 historical points-training rows used
nonzero opponent-defense features from the latest stored team snapshots. That
gave older games future defense information and made the recorded validation
metric unreliable as a clean historical test. Point and distributional training
now build each team's profile from its preceding completed games. The profile
requires five prior complete team boxes and uses a ten-game window; early games
are explicitly missing. The corrected training build supplied a prior defense
profile on 71,882 of 71,979 points rows (99.9%). Same-day games cannot feed
one another. The live inference feature names and stored artifacts are
unchanged. Model loading now
requires an artifact trained with the new dated context, at least 100 later
validation rows, MAE better than the recent-five-game baseline, and the
existing stat-specific MAE limit. Thus all legacy artifacts, including the
4.603-MAE points artifact, fall back to the heuristic projection until they
are retrained and pass. Failed retrains remain inactive and do not replace a
qualified active model. New projection retrains use a separate, earlier
early-stopping period; their later validation period is not used in fitting.

Historical game totals were un-timestamped closing quotes, which cannot be
verified as available when the app would make a recommendation. Point and
distributional training now leave that feature unavailable. An earlier
challenger run that included closing totals was discarded. The results below
are from the corrected, read-only rerun.
Legacy distributional artifacts are held from inference for the same reason.

The read-only `scripts/evaluate_matchup_challenger.py` compares the current
feature set with and without dated defense, plus a simpler-tree variant, against
the same later-date games. It holds out an earlier tail for XGBoost early
stopping and never writes an active model. Its measured results are saved
outside the repository in `sports_research/nba/2026-10-01-matchup-patterns/`.
On 15,448 games after December 31, 2025, the corrected MAE results were:

| Candidate | Points MAE | 90th-percentile absolute error |
|---|---:|---:|
| Recent-five-game average | 4.8078 | — |
| Current feature set, no defense | 4.6385 | 9.8804 |
| Dated defense, current settings | 4.6190 | 9.8422 |
| Dated defense, simpler trees | **4.5989** | **9.7850** |

The simpler dated-defense candidate beats the recent-five baseline by 0.2089
points per game (4.3%) but misses the existing 3.5 gate by 1.0989 points.
The dated defense features improve error by only 0.0195 under current settings.
The historical pace and rating fields are possession proxies computed from
player box totals; they do not encode coverages or defensive assignments and
should not be interpreted as measured scheme effects.
These are outcome-prediction errors, not prop-profit results; no historical
offered lines were available for a price-based test. The points MAE requirement
stays 3.5 and no model is promoted from this search.
Because candidate choices were informed by the prior matchup screen, a future
season remains necessary as an untouched prospective check.

## Verification

The full unittest run passed on October 1: 1,369 tests, one skipped, with
84% coverage across `app/`. Ruff, Bandit, and the diff whitespace check passed.
The Chromium desktop analysis workflow passed against a clean migrated SQLite
database. The local production-readiness check still reports **NOT READY FOR
PRODUCTION** for the data and model gates above. PostgreSQL migration and
restored-backup checks were not part of this slice.
