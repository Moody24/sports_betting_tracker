# Basketball launch baseline

Scope: owner-operated local tracker, followed by an invited beta. The NBA's
[official schedule release](https://www.nba.com/news/2026-27-nba-regular-season-schedule)
sets opening day at **October 20, 2026**. This is a product-readiness target,
not a claim that the data providers, hosting, or models are ready.

The source checkout was clean at `f6c6046` on
`codex/structural-refactor-2026-10-01`. Work takes place on
`codex/basketball-launch-2026-10-07` in an isolated clone. Its Git history
includes eight existing commits (six implementation and two documentation)
after GitHub main `d03cc8e`; none were rewritten.
No original database, secrets, model artifacts, or unrelated worktree was copied
or changed. New Git commits use ordinary user authorship with no AI trailers.

## Implementation plan and acceptance criteria

| Work | Acceptance criteria | Evidence |
| --- | --- | --- |
| Inspect current product | Read architecture/run instructions, source-rights review, before desktop/mobile screenshots | Source review linked below; 4 baseline Playwright tests passed |
| Redesign | Coherent shell, dashboard, ledger, NBA pages, forms; usable 320–1440px; keyboard/focus/contrast checks; existing controls retained | Final browser evidence recorded below |
| Shared Games feed | One collector every 30 seconds; durable change journal; authenticated shared SSE; replay/reset, freshness, hidden-tab cleanup and bounded resources; no provider calls per viewer | Collector/stream/browser integration tests |
| Cache correctness | Provider outage cannot become an all-day empty slate; retries recover; ET rollover invalidates stale state; failed fetch cannot renew freshness | Deterministic ingestion regression tests |
| Quote integrity | Active games never use archived prop JSON as current; replayed source observations cannot duplicate quote rows; preserve user bet prices | Prop cache and snapshot replay tests |
| Source access | Disabled/unconfigured providers are explicit; ESPN requires operator rights basis; no new subscription or account; secrets absent from error messages | Provider transport tests and data-status tests |
| Request safeguards | Reject malformed/nonfinite payloads before provider traffic or DB writes; bound card/game fanout; client chunks sequentially and backs off | Request-validation unit and browser tests |
| Privacy | Production cannot disable Secure cookies; private/auth responses use no-store | Security tests |
| Delivery | Lint/security/secret checks, unit coverage, browser flows/accessibility/responsiveness, reviewed screenshots; concise commits; task branch push verified remotely | Final checks recorded below |

## Launch gaps, in priority order

| Priority | Gap | Resolution / gate |
| --- | --- | --- |
| P0 | Live credentials and end-to-end provider coverage unverified | Free BALLDONTLIE Games implementation selected and disabled without key. Account/key approval and secure configuration remain pending; odds are separate. Manual records remain usable. |
| P0 | Existing source errors could look like a real empty slate or fresh props | Fix cache/error semantics and show separate availability and actual fetch timestamps. |
| P0 | Provider collection/redistribution rights | Default ESPN transport off; review source-specific use and audience before enabling. A configuration flag is not a license. |
| P1 | Malformed JSON/nonfinite prices and progress fanout | Validate entire request before work; cap 100 cards/15 games; sequential client batches. |
| P1 | Duplicate odds snapshots on retry | Deterministic durable source key; DB uniqueness and savepoint handling. |
| P1 | Unclear/dense UI and weak mobile navigation | Redesign existing Jinja/CSS components, preserve workflows, verify real browsers. |
| P1 | Production privacy can be weakened by environment flags | Fail closed on insecure cookie override; no-store private/auth responses. |
| P1 | Invite policy, backups/restore, monitoring, scheduler ownership | Operational gates before invited beta; no deployment or production data changes here. |
| P1 | Models not qualified for profitability claims | Retain existing point-in-time validation and abstention gates. Do not lower thresholds or claim proven ROI. |
| P2 | No authorized/configured external webhook source | BALLDONTLIE documents free game-start/end pushes, with limited quota; keep provider webhooks unconfigured pending source approval. Existing singleton polling coordinator reconciles game changes and runs internal final-game workflows. |
| P2 | Broader modularization | Extract request validation, source-access policy and presentation status; retain modular monolith and avoid schema/platform rewrite. |

## Source and event decisions

See [source review](2026-10-07-data-source-review.md) for primary-source links,
license limitations, source latency, quotas and alternatives. Public access,
software licensing and permission to redistribute a sports feed are different.

ESPN transport is off by default. `ESPN_ACCESS_APPROVED=true` and
`ESPN_RIGHTS_BASIS` are an operator admission record, to be set only after
permission is established. Direct NBA Stats collection separately requires
`NBA_ACCESS_APPROVED` and `NBA_RIGHTS_BASIS`. The application never establishes
that permission itself. The Odds API remains optional; the example key is blank and no key was
copied into this checkout. A configured credential is not a freshness claim.

The existing APScheduler coordinator owns polling. It is a single-owner
process, not a provider push connection. It detects final games, reconciles
durable state, then runs grading/history/postmortem work. This task does not
expose a speculative webhook receiver without a source. If an authorized
provider is chosen, add its signature verification, replay/idempotency rules,
namespace mapping and reconciliation tests before enabling its receiver.

The new BALLDONTLIE collector polls once every 30 seconds and emits durable internal
change events. Authenticated browser SSE shares one DB reader per web process,
with bounded replay, heartbeat, reconnect, backoff and local JSON fallback. No
provider pushes are claimed. Source delay has no verified SLA.

The current legacy ESPN IDs must not be silently reused as BALLDONTLIE IDs.
That mapping and current/closing quote evidence are explicit integration work,
not something a source key alone solves.

## Design and skill review

Design revised after screenshot feedback: compact horizontal NBA navigation
without a sidebar, neutral Arial/Helvetica product typography with aligned
tabular numbers, smaller headings, tighter analytical panels, and original
functional SVGs. PropFinder's public dashboard imagery informed market hierarchy
and density; its logos/artwork were not copied. Manual entry is collapsed below
the game/player-prop board. Unavailable or stale markets remain explicit.

Public PropFinder layout and its published dashboard image were visually
inspected. DraftKings' page structure was available to the web reader, but its
browser page returned 403; FanDuel also blocked access. No access control was
bypassed or unavailable visual layout claimed.

The requested [Taste skill](https://github.com/Leonxlnx/taste-skill) was cloned
at commit `b482f7a970abb98c4108d4a9f761e458c64cefc8` and configured through
[the project compatibility entrypoint](../../.agents/skills/taste/SKILL.md).
Its unchanged upstream Markdown and MIT license remain with the entrypoint.
Taste governs public landing guidance; dashboard, table and form work stays
with frontend-design and the project definition of done. No framework or
animation dependency was installed.

The initial `skillguard` CLI was unavailable (exit127), so the first review was
manual. After the skill catalog refresh exposed the bundled scanner, the pinned
source and final package were scanned successfully: zero active and zero
suppressed findings. This is a risk scan, not a universal safety guarantee.
See [setup and provenance](../frontend/taste-skill.md) and
[scanner report](../frontend/taste-skill-scan.md).

## Implemented baseline

- Shared Games collection, durable snapshot/journal and authenticated SSE are
  implemented, disabled until an approved key is supplied. One collector means
  browsers do not multiply upstream requests; fresh/stale/unavailable and ET
  rollover are explicit. Gunicorn override guards preserve streaming capacity.
- Game-first recording begins with a verified scheduled/live event and an
  available player prop. Source observations and signed evidence accompany the
  selection; refreshed quotes never silently replace the recorded line or price.
  Changed/expired quotes need explicit review. Manual history is secondary.
- Duplicate selections and simultaneous submits are blocked; durable user-scoped
  request receipts support safe retries after an uncertain save.
- Manual game and player records remain usable without providers. Recorded prices
  survive refresh and retain manual provenance; no default odds are fabricated.
- Current/scored/builder quotes and current game markets require aware provider
  observations within 10 minutes.
  Archived data remains available only in explicit historical/finalized paths.
- Live snapshot retries use a deterministic database-unique observation key.
- Shared caches preserve last success on failure, retry with bounded delay, and
  invalidate at ET midnight. Empty/done coordinator states recheck after 15 minutes.
- Live progress requests have bounded fanout, one <=3s attempt per game and a 12s
  batch budget; deferred cards retry first. The summary cache is capped at 512.
- Provider event IDs stay in their namespaces; unmatched Odds API fixtures never
  become ESPN IDs. Upcoming dates use aware ET timestamps.
- Separate source-access, request-validation, quote evidence, retry receipts,
  game-picker and status modules clarify ownership.
- Midnight-crossing collector replies retain their actual fetched ET day. DB
  outages invalidate stream freshness and queued snapshots; recovery resets the
  full view even when its revision has not changed.
- Model2 probability adjustment now learns only from its calibration partition;
  held-out tests evaluate the deployed postprocessing. Unversioned legacy bias
  is ignored. Calibrated artifact persistence failure keeps the incumbent instead
  of replacing it with an uncalibrated fallback. No model was trained/activated.
- Werkzeug updated from 3.1.6 to 3.1.9 after dependency audit identified
  CVE-2026-102598; see the [upstream release notes](https://werkzeug.palletsprojects.com/en/stable/changes/#version-3-1-9).

## Expansion boundary

[NFL/NHL roadmap and UFC research backlog](2026-10-07-sport-expansion-roadmap.md)
follow basketball verification and operational readiness. They are not delivered
sports coverage. Authorized data, provider IDs, event-grouped temporal evaluation,
calibration and promotion gates precede training. Existing artifacts require
requalification; no profitability claim is made.

## Verification results

Local verification on October 7, 2026:

- Full unit suite: **1,542 tests completed successfully in 360.749 seconds**, with
  one PostgreSQL-only test skipped locally. Application coverage: **85%**.
- **94 unique Playwright cases passed**: 48 accessibility/responsive/stream,
  12 visual comparisons without baseline updates, and 34 guided/manual/event/
  progress/security/flow cases. Additional populated geometry checks passed at
  320, 375, 412 and 1440 pixel widths.
- A real Gunicorn HTTP smoke passed **11 checks**: authenticated snapshots,
  15-second heartbeats, shared-reader delivery, durable replay, eight-stream
  capacity, ninth-stream 429, and ordinary HTTP responsiveness at capacity.
  The measured ordinary request took 134.6ms; no outbound provider requests or
  held database connections were observed between polls. This is a local smoke,
  not a production capacity or latency guarantee.
- SQLite migrations reached `f8b21c09a74d`; `flask db check` found no model drift.
- Ruff, Bandit, staged-file and Git-history secret scans, dependency audit,
  Python compilation, JavaScript syntax, 18 Jinja template parses, and diff
  whitespace checks passed.
- No standalone frontend build or static type-check task is configured.
  PostgreSQL migration/bulk-copy and Linux browser verification run in CI;
  their exact-commit status belongs to the draft PR, not this local result.

An earlier full run found an obsolete assertion expecting direct research-quote
queueing. The assertion now requires navigation to the verified builder; its
17-test module and the full rerun above passed.

A final independent review then reproduced a withdrawal race: a delayed quote
reply could restore an unavailable market. A durable receipt-time watermark now
requires strictly newer source evidence before reopening; rejected replies carry
no selectable token. All **25 guided-recording tests passed** after that final
fix, including replay and recovery regressions. The full unit result above
precedes this narrow fix; CI reruns the whole suite on the committed revision.
An unchanged provider timestamp conservatively remains unavailable after
withdrawal until newer evidence arrives.

UI fixtures use a disposable SQLite database and explicit provider-network
blocking. Fixture screenshots are visibly labeled test evidence, not current
sports data. The user's PostgreSQL database, secrets and model artifacts were
not used or changed. Real credential coverage, source latency, permitted use,
production proxy behavior and deployment remain unverified launch gates.
