# Edge Tracker local and launch audit — 2026-10-01

Scope: NBA tracker for the owner first, followed by a small invited beta. The
2026-27 regular season opens [October 20, 2026](https://www.nba.com/news/2026-27-nba-regular-season-schedule).

## Current verdict

The ledger, analysis UI, migration chain, and model inference code exist and
run locally. The project is not yet verified as a season-ready automated
recommendation service or a hosted beta. The distinction matters: historical
prediction accuracy is measured, but no local real-bet or decision/closing-quote
history exists to establish live ROI, calibration against offered lines, or
safe promotion of recommendations.

| Release use | Verdict | Evidence-based limit |
|---|---|---|
| Owner's local ledger and historical analysis | Usable now | PostgreSQL-backed app and database checks pass; scheduled data collection remains off. |
| Fully automated local NBA projections | Not ready | Current operational logs and defense snapshots are empty; points validation misses its internal threshold; game-day jobs have not been exercised this season. |
| Small invited beta | Not ready | Registration is open and account lifecycle is incomplete; private access has not been configured or tested. |
| Public hosted service | Not ready | No active deployment, managed database, durable model artifact store, or live-data evaluation evidence. |

## Evidence checked on this date

| Area | Current evidence | Meaning |
|---|---|
| Local data | 1 user, 0 bets, 79,603 historical NBA game logs, 3,962 historical game odds rows, 1,637,800 scenario splits | Historical modeling input is present; personal bet outcomes are not. |
| Quotes and evaluations | 0 `odds_snapshots`, 0 `model_evaluation_run` | Live line value, CLV, and ROI cannot be verified from this database. |
| Models | 17 active artifact records; all six projection regressors loaded and produced a finite inference result | Files are executable locally; this is a runtime smoke test, not an accuracy or profitability test. |
| Stored dated validation | 56,531 training and 15,448 post-cutoff validation samples for projection models; cutoff 2025-12-31 | This is useful historical evidence, but it is not an untouched 2026-27 prospective test. |
| Projection validation MAE | Points 4.603, rebounds 1.902, assists 1.368, threes 0.910, steals 0.731, blocks 0.520 | Error is measured in each stat's units; these numbers do not establish a betting edge. |
| Built-in production readiness | 5 OK, 4 warnings, 3 critical failures | Points MAE 4.603 exceeds its 3.5 threshold. There are 0 resolved picks with context, 0 active `PlayerGameLog` rows, and 0 `TeamDefenseSnapshot` rows. Model 2 and real-line evaluation are not ready. |
| Local database | SQLite backup integrity passed; PostgreSQL 16 runs on a private Unix socket with checksums; all 16 tables copied and independently validated | `.env` now points at PostgreSQL. A PostgreSQL dump was restored into a disposable database and matched all source counts and domain metrics. |
| Provider | Odds API credential returned HTTP 200 with `basketball_nba` active and 500 credits remaining; ESPN returned an empty October 1 scoreboard | Connectivity works; no live prop quote or game-day behavior was verified. |
| App smoke | PostgreSQL-backed Gunicorn returned HTTP 200 for `/health`, `/ready`, `/auth/login`, and `/`; the points model loaded | Core server startup works locally. This does not exercise every authenticated workflow. |
| Test gates | 1,332 unit tests passed; application coverage 85%; Ruff, Bandit, dependency audit, migration drift, and PostgreSQL bulk-copy integration passed. A clean, freshly migrated PostgreSQL browser run passed 58/58 checks. | Current functional checks pass. They do not establish live game-day data or model profitability. |

The readiness output is saved at
`instance/backups/prod-readiness-2026-10-01.txt`. The stored metrics are in
`model_metadata`; the limits for Model 2 and rolling
backtests are enforced in `app/services/pick_quality_model.py` and
`app/services/rolling_backtest.py`. Model 2 requires at least 400 clean resolved
picks. The rolling quote evaluation requires sufficient real decisions, games,
and decision/closing coverage. Zero local bets and quote snapshots cannot meet
those gates.

## Defects found and repaired in this audit

- A SQLite migration assumed a named `bet_postmortem` constraint that the
  existing local database had recorded as a unique index. The migration now
  handles either representation. A failed attempt's empty temporary table was
  removed after the source table and backup were verified.
- Legacy local indexes drifted from the ORM schema. A new migration reconciles
  them, and both SQLite and PostgreSQL report no pending schema operations.
- Four valid model-version labels exceeded the PostgreSQL `VARCHAR(40)` limit
  that SQLite did not enforce. The field is now 80 characters, with a guarded
  migration and a source-length preflight in the copy command.
- The pinned `nba_api` 1.5.2 static active list missed 203 of 583 distinct
  2025-26 historical names by exact spelling. Upgrading to 1.11.4, consulting
  inactive players, normalizing accents, resolving a unique active-name
  collision, and adding verified NBA.com IDs reduced unresolved names to 23,
  none with 30 or more 2025-26 games. Unresolved completed-game names now log a
  warning. This remains a seasonal roster-maintenance task.
- UI CI installed Python packages globally while its Playwright setup invokes
  `.venv` binaries. CI now creates that virtual environment.
- Playwright's local PostgreSQL bootstrap interpolated a URL with `&` into an
  unquoted shell command, silently skipping migration while still starting the
  web server. Environment values and the disposable SQLite path are now shell
  quoted; the clean PostgreSQL browser run replays migrations from zero.
- The local PostgreSQL helper now creates a private, verified custom-format
  backup with one command; database-copy JSON reports are written mode 0600.

## Release gates before October 20

1. Keep the current release checks green as code changes. The October 1 suite
   passed 1,332 unit tests with 85% coverage and 58/58 browser checks on a
   clean PostgreSQL migration. Rerun both gates after later application or UI
   changes.
2. Address model/data readiness honestly. Investigate why the points regressor
   misses its configured error threshold, compare it with a naive baseline,
   and populate current-season operational
   logs, team-defense context, and real decision/closing quotes as games become
   available. Historical-game-log rows do not populate the operational
   `PlayerGameLog` cache used by the readiness command.
3. Decide the local operating mode. The current `.env` disables the scheduler
   and does not enable ML projections. Validate one-worker job ownership and
   data freshness with scheduled jobs enabled before calling local operation
   fully automated.
4. Collect genuine dated decision and closing quotes, actual outcomes, and
   provider-availability evidence. Keep betting recommendations labeled
   experimental until the evaluation gates pass. An opening-night deadline
   cannot replace the missing live sample.
5. For the invited beta, restrict registration/invitations, define account
   removal and backups, then serve the one-worker app privately through an
   authenticated VPN such as Tailscale Serve. Do not expose the Flask
   development server directly.
6. For public hosting later, provision managed PostgreSQL, persistent model
   artifact storage, secrets, a domain/TLS, monitoring, and a backup/restore
   drill. The current metadata paths point to local model files, so artifacts
   must be moved or materialized through the configured object-store contract
   before a remote host can run inference. Railway access is currently inactive.

The codebase is about 24,000 Python lines across 100 app files. A safe cleanup
should follow measured failures and architecture boundaries. This audit fixed
specific portability, identity, and CI defects; it does not claim that every
module has been rewritten or that all live provider paths have been exercised.

The original SQLite image is retained under `instance/backups/`, along with a
PostgreSQL custom-format dump and JSON copy/restore validation reports. These
files are local, access-restricted, and excluded from Git. Start the local
database with `scripts/local_postgres.sh start`, then the one-worker app with
`.venv/bin/python scripts/run_local.py`. The latter enables jobs only when
passed `--with-scheduler`.
