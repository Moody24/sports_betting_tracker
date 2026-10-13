# Season-readiness implementation audit — 2026-10-01

The review covered routes and browser flows, provider adapters, the database and
migrations, scheduled jobs, projection and recommendation services, model
evaluation, tests, CI, and the local deployment path. The initial baseline is
in `2026-10-01-local-launch-audit.md`. This report separates repaired defects
from launch evidence that cannot exist before the 2026–27 season.

| Priority | Workflow | Finding and current disposition |
|---|---|---|
| P0 | Automatic picks | Missing injury reports, team assignments, player history, current-season opponent defense, or fresh offered quotes could previously flow into scored props. Analysis now retains warnings; production and paper auto-pick creation abstain until all inputs are verified. Model 2 context now reads verified ESPN history without an NBA ID guess. Prior-season defense can inform analysis but cannot verify an automatic pick. The historical points MAE and absent prospective outcomes still prevent a production-ready claim. |
| P0 | Data freshness | Failed defense fetches persisted league-average values with today's snapshot date, and failed injury fetches copied older statuses forward. Both behaviors were removed. Durable fetch receipts distinguish success, zero rows, failure, and conflicting same-day injury sources. |
| P1 | Player identity | Permanent ESPN-ID history was invisible to live projections and player detail, while the operational NBA-ID cache was empty. Projections, recent form, hit rates, and player detail now prefer unique ESPN identity and permanent as-of history. A dedicated crosswalk stores 809 observed IDs, including 10 links backed by verified NBA ID overrides; ambiguous names abstain. |
| P1 | Coverage | The old hoopR raw-file URL was archival. Player imports now target the active SportsDataverse release. Team and injury release adapters preserve release versions and as-of dates; observed prior-season defense is available for 30 NBA teams. Historical defense is a labeled prior, not a current-season statistic. |
| P1 | Database portability | A legacy SQLite migration tried to drop an unnamed unique constraint. The guarded migration now replays from an empty SQLite database. PostgreSQL was backed up, upgraded, and checked for schema drift. |
| P1 | Launch operation | The local app now runs with one Gunicorn worker and 22 scheduler jobs under an exclusive local lock. `/health`, `/ready`, and the login route respond. Game-day jobs, feed availability, quote capture, and restart recovery still need observation on an actual slate before unattended operation. |
| P1 | Model evidence | The points regressor's post-cutoff MAE is 4.603 against the project's 3.5 threshold. On the same 15,448-row time-ordered holdout, a global-mean baseline scored 6.801 and a recent-five-game baseline scored 4.808. The model beats the latter by about 4.3%, which does not establish an edge against offered lines. The weekly retrain guard now counts the permanent history that actually feeds training. |
| P2 | Invited beta | Registration remains open and account removal is incomplete. Restrict access, verify provider redistribution rights, and restore a hosted backup before inviting others. |
| P2 | Maintainability | The largest remaining modules are `nba_service`, `model_commands`, `market_recommender`, `value_detector`, `scheduler`, and `models`. Continue extracting cohesive provider normalization and decisions in behavior-preserving slices; avoid mass cosmetic rewrites. |

The [player](https://github.com/sportsdataverse/sportsdataverse-data/releases/tag/espn_nba_player_boxscores),
[team](https://github.com/sportsdataverse/sportsdataverse-data/releases/tag/espn_nba_team_boxscores),
and [injury](https://github.com/sportsdataverse/sportsdataverse-data/releases/tag/espn_nba_injuries)
releases are useful for reproducible history. SportsDataverse's MIT software
license does not necessarily cover upstream ESPN data redistribution. The
releases do not include sportsbook player-prop quotes. [The Odds API's paid
historical event-odds endpoint](https://the-odds-api.com/liveapi/guides/v4/index.html#historical-event-odds)
offers player-prop snapshots from May 2023 onward, but none have been imported
or coverage-checked here. The existing CSV/JSON quote importer can accept
licensed history; scheduled jobs will capture new decision and close quotes.
Both paths need timestamp, bookmaker, market, and game joins checked before
any ROI or CLV claim.

[hoopR's player crosswalk](https://github.com/sportsdataverse/hoopR/releases)
could seed a manual NBA-ID review queue, but its player matching is fuzzy.
It is not accepted as automatic identity evidence; Model 2 features and
automatic-pick context now use unique ESPN history directly.

The local database currently contains 79,603 historical NBA player-game rows,
809 ESPN player identities, 30 observed prior-season defense profiles, and 56
injury rows from a release dated October 1. `PlayerGameLog`, `OddsSnapshot`,
and resolved local pick context remain empty. The current slate has no NBA
games; readiness reports the prior-season and missing prospective data as
warnings rather than labeling them fresh.

The `/ready` endpoint reports defense observation through April 12 and marks
it stale, even though the release was fetched on October 1.

The October 1 coordinator dry run returned `dormant`, consistent with a zero-game
NBA slate. The separate production-readiness command reported seven OK, six
warnings, and one failure: Model 2 has zero resolved picks with context against
its 300-pick gate. `USE_ML_PROJECTIONS` remains disabled locally.

The 2025–26 release re-import completed with zero new rows, 26,680 duplicate
rows skipped, and zero updates. An independent grouping of the permanent store
found zero duplicate `(game_id, player_name)` pairs. The 2026–27 player
release returned 404 and is recorded as unpublished, while the direct ESPN
final-game path can append played games in the meantime. A PostgreSQL backup
restored to a disposable database with 79,603 history rows, 809 identities,
30 defense rows, 56 injury rows, and two source receipts. The full migration
chain and schema drift check passed on a fresh PostgreSQL database, which was
then removed. Clean SQLite migrations passed separately.

The browser run passed 58 desktop/mobile workflow, visual, and accessibility
checks. The full unittest run passed 1,351 tests with one skip; application
coverage was 82%. The focused integrity suite passed 17 tests after the final
injury-source retry fix. Ruff, Bandit, `pip-audit`, and migration checks passed.

Next release gates: verify the first actual 2026–27 final-game append and
same-day injury/defense refresh, then collect real decision and closing quotes.
Model 2 requires its existing resolved-pick gate. No threshold is lowered to
meet the calendar deadline.
