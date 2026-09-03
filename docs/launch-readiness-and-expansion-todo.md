# Edge Tracker Completion and Launch Readiness

Last reconciled: 2026-09-03

This is the current completion record for the NBA application. It replaces the
old speculative implementation backlog. The repository-owned launch foundation
is complete; the remaining gates require deployment access, third-party data, or
product/legal decisions and must not be represented as unfinished code.

The architecture source of truth is
[`docs/architecture/system-contract.md`](architecture/system-contract.md). The
deliberately retained engineering debt is listed only in
[`docs/tech-debt-register.md`](tech-debt-register.md).

## Status summary

| Area | Repository status | External or operational gate |
|---|---|---|
| NBA ledger and analysis | Complete | Live lines require an Odds API subscription/key and provider availability. |
| Sheet UI | Complete | None for local use; final public brand/domain choices may change marketing assets. |
| Security and privacy foundation | Complete for the one-worker baseline | A hosted multi-user product needs an explicit account-lifecycle policy and legal review. |
| Route and error contracts | Complete | Hosted error monitoring requires a monitoring vendor and credentials. |
| SQLite/PostgreSQL portability | Complete and CI-enforced | A real cutover requires a provisioned empty PostgreSQL database, backup window, and operator access. |
| Deployment topology | Safe one-worker contract complete | Railway is inactive; staging/production services, secrets, backups, and domain are not provisioned. |
| ML engineering | Training, inference, calibration, and promotion gates complete | Recommendation promotion requires licensed real decision/closing quotes and at least 400 clean dated resolved picks. |
| MLB/NFL | Intentionally not implemented | Each sport needs approved product scope, licensed providers, sport-specific historical data, grading rules, and models. |
| Webhooks | Not applicable to current providers | Implement only after a selected provider supplies a real signed webhook contract. |
| Shared Redis cache | Not required for the one-worker baseline | Required before deliberately scaling to multiple web workers. |

## Completed repository scope

### Application and security

- [x] Registration, login, POST logout, password hashing, CSRF protection, object
  ownership, generic login failures, and bounded password input.
- [x] Production cookie defaults, idle/absolute session lifetimes, strong session
  protection, remember-cookie policy, and fail-closed production configuration.
- [x] Login/registration throttling and a topology check that rejects process-local
  rate limiting with multiple production workers.
- [x] Allowlisted and bounded UX telemetry with documented CSRF exemption.
- [x] Browser-state inventory and logout clearing for current and retired parlay
  queue keys.
- [x] Security headers, owned local fonts/icons, environment-contract tests,
  dependency auditing, static security analysis, and tracked/history secret scans.
- [x] Explicit policy catalog for every non-static route, including method,
  authentication, CSRF posture, response type, owner, and rate class.

### Errors, request correlation, and public surface

- [x] Request IDs assigned at the application edge and returned on responses.
- [x] Safe negotiated handlers for 400, 401, 403, 404, 405, 429, 500, 502, 503,
  and 505.
- [x] Stable `ApiErrorV1` JSON and one accessible HTML error view; private exception
  or provider details are never reflected.
- [x] Database rollback on server errors and preservation of `Retry-After`.
- [x] Public home, methodology, responsible-gambling, privacy, terms, data-source,
  and about pages with truthful copy.
- [x] Canonical origin validation, descriptions, Open Graph/Twitter metadata,
  public structured data, accessible breadcrumbs, dynamic `robots.txt`, and a
  public-only `sitemap.xml`.
- [x] `noindex, nofollow` on authenticated and authentication surfaces.

### UI and browser behavior

- [x] Dashboard, Position Log, Prop Analysis, NBA Today, Stat Analysis, unified Bet
  Builder/import, authentication, public pages, and errors use one Sheet grammar.
- [x] Duplicate builder output and dead legacy UI assets removed.
- [x] CSS class manifest regenerated from live templates and obvious unused legacy
  rules removed; behavior-created classes remain guarded rather than guessed dead.
- [x] Functional, accessibility, responsive, logout-isolation, and reviewed visual
  browser baselines cover desktop and mobile.

### Data, architecture, and operations

- [x] Canonical dependency/state/ownership contract enforced by architecture tests.
- [x] NBA provider normalization, user-scoped repositories/services, grading,
  snapshots, projections, model evaluation, and 22-job scheduler registry.
- [x] ET business-date rules and UTC persisted event timestamps.
- [x] Fresh SQLite migration-chain replay and PostgreSQL 16 Alembic chain in CI.
- [x] Guarded `flask migrate-sqlite-to-postgres` command with engine/schema/empty
  target checks, transactional batches, primary-key preservation, PostgreSQL
  sequence repair, count/domain/foreign-key validation, and sanitized JSON report.
- [x] Blocking hosted pre-deploy migrations, bounded database pool defaults,
  `/health`, `/ready`, Gunicorn, Docker, and one-worker/single-scheduler ownership
  contracts.
- [x] Backup, restore, cutover, rollback, incident-response, and retraining runbooks.

### ML and technical-debt cleanup

- [x] Six projection regressors and seven distributional heads/calibrators retrained
  from 79,603 permanent historical rows.
- [x] Leakage-safe temporal splits, rolling-origin evaluation, calibration, ROI,
  CLV, drawdown, Kelly, and two-run promotion rules.
- [x] Real decision/closing quote ingestion and event-relative T-60/T-10 capture
  contracts, with synthetic rows excluded from promotion evidence.
- [x] Model artifact metadata/rollback protection and stale-artifact cleanup.
- [x] All Ruff complexity findings eliminated; oversized workflows decomposed and
  the former service-test monolith split by domain.
- [x] Stale virtual environments, generated browser artifacts, unused model files,
  contradictory runbooks, and the unrelated interview-preparation document removed.

## Canonical release gate

A repository release is eligible only when all commands below pass:

```bash
.venv/bin/ruff check .
.venv/bin/bandit -q -r app -x tests -ll
.venv/bin/pip-audit -r requirements.txt
SECRET_KEY=test .venv/bin/python -m coverage run -m unittest discover -s tests -v
.venv/bin/python -m coverage report --include="app/*"
./scripts/predeploy_guardrails.sh
npx playwright test
```

CI additionally exercises the full Alembic chain on PostgreSQL 16. The local
machine does not need a resident PostgreSQL server or Docker daemon to duplicate
that provider-specific job.

## External completion gates

These are ordered. Do not skip ahead or convert them into placeholder code.

### 1. Decide the release mode

Owner: product/operator.

- Keep the current local/private NBA tool, or authorize a hosted multi-user
  service.
- Choose the public domain and canonical origin.
- Choose whether closing values shown on personal bets come from a named book,
  consensus, or manual entry. The database deliberately records unknown until
  that semantic choice is made.

If the product remains local/private, no hosted account, email, Redis, public
legal, or production monitoring work is required.

### 2. Provision hosted infrastructure, if authorized

Owner: operator with Railway/database/DNS access.

- Create isolated staging and production environments and empty PostgreSQL
  databases.
- Store `SECRET_KEY`, `DATABASE_URL`, `PUBLIC_BASE_URL`, provider keys, and any
  monitoring/email credentials only in service variables.
- Keep `WEB_CONCURRENCY=1`, `SCHEDULER_ENABLED=false` on web, and exactly one
  scheduler owner. Add Redis before raising web concurrency.
- Configure automated database backups and complete a documented restore drill.
- Run the guarded SQLite copy in staging, inspect its sanitized report, and rehearse
  rollback before the production maintenance window.
- Run smoke tests behind the real proxy/TLS/domain and observe error, latency,
  connection, scheduler, and data-count signals.

### 3. Complete public-product policy, if authorized

Owner: product/legal/security.

- Obtain jurisdiction-specific review of terms, privacy, data attribution, and
  responsible-gambling content.
- Decide retention, account deletion/export, duplicate-registration disclosure,
  password recovery, email verification, multi-device session revocation, and
  abuse-response policy.
- Select email and monitoring providers before implementing integrations; do not
  invent credentials, delivery guarantees, or data-processing terms.
- Create the final favicon/social preview and register search tooling only after
  the domain and brand are final.

### 4. Supply real model evidence

Owner: data/product with licensed-data authority.

- Import licensed historical book-specific player-prop decision and closing quotes,
  or accumulate them prospectively.
- Accumulate at least 400 clean dated resolved real-line picks for Model 2.
- Maintain point-in-time injury/lineup provenance where it is used.
- Run consecutive shadow evaluations and promote only when the existing calibration,
  coverage, ROI, CLV, drawdown, sample-independence, and two-run gates pass.

Until then, projections are analytical estimates and profitability is unproven.
No UI or public copy may imply guaranteed value or validated returns.

### 5. Authorize any new sport separately

Owner: product/data/legal.

MLB and NFL are separate products, not configuration flags. Before either vertical
slice begins, approve its markets and grading rules; license schedule, line, result,
injury/lineup, and historical feature sources; and define provider quotas and
attribution. Then build sport-aware identifiers, schemas, adapters, features,
models, calibration, routes, schedules, and shadow gates. NBA artifacts or features
must never be relabeled as MLB/NFL evidence.

## Deliberately deferred architecture

- A shared cache is a scale feature. PostgreSQL remains durable truth and the
  current process-local caches are correct under the enforced one-worker baseline.
- A webhook inbox is an integration feature. Polling remains authoritative until
  a contracted provider exposes signed events that justify replay, idempotency,
  retry, and reconciliation machinery.
- Additional authentication features are hosted-product policy work. Implementing
  them without a release decision, email provider, retention policy, and legal
  basis would create unused code and contradict the repository-cleanup goal.

## Completion rule

Repository work is complete when the canonical gate is green and CI passes after
push. Hosted launch, model promotion, and multi-sport expansion remain blocked
until their named external inputs exist. When an external decision is made, open a
small implementation issue with the owner, evidence, and acceptance test rather
than reviving the retired speculative backlog.
