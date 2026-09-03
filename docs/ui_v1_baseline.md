# Edge Tracker UI Release Baseline

Last verified: 2026-09-03

This is the current visual and interaction contract for the completed Sheet UI.
The previous card-based baseline is retired. Automated enforcement lives in the
template contract tests and the Playwright accessibility, responsive, functional,
and visual suites.

## Global release gates

1. Templates may use inline `style` only for server-computed CSS custom
   properties. `tests/test_template_inline_styles.py` enforces the rule.
2. Structural surfaces use the shared Sheet vocabulary: sheet head, control
   bar, column band, ruled rows, slip, states, notices, tags, and actions.
3. Primary actions use `.act.act-primary`; secondary actions use `.act`.
4. Focus is visible, semantic labels remain present, and keyboard operation does
   not depend on hover.
5. Core, public, authentication, and error pages do not overflow at the supported
   compact widths. Reduced-motion preferences are respected.
6. Private pages emit `noindex, nofollow`. Only the deliberate public surface
   receives canonical metadata and structured data.
7. Visual baselines are regenerated only for an intentional, reviewed UI change.

Quick checks:

```bash
SECRET_KEY=test .venv/bin/python -m unittest \
  tests.test_template_inline_styles \
  tests.test_ui_class_audit \
  tests.test_ui_framework_contract
PATH=/path/to/node/bin:$PATH npx playwright test
```

## Surface contracts

### Dashboard

- The summary is a single ledger band with one lead figure, not a row of cards.
- Model Recommendations, bankroll, performance, and recent positions have one
  clear hierarchy and preserve honest empty states.
- Tables and figures remain readable without horizontal document overflow.

### My Bets — the Position Log

- Pending, live, won, lost, and push states use the shared tag vocabulary.
- Live rows expose status, score, stat progress, pace, trend, and freshness.
- Parlays group related legs and retain keyboard-operable disclosures/actions.
- CLV is absent when no closing price was captured; absence is never rendered as
  zero.

### Prop Analysis — the Board

- Model probability is the lead figure; market price, fair price, edge,
  projection, and line remain aligned secondary facts.
- Populated rows include the `.pp` recent-form display with truthful direction
  and result semantics.
- Loading, provider failure, no-data, and filtered-zero states are distinct.
- The player modal remains an overlay layer and keeps dense facts accessible.

### NBA Today

- Current ET slate, score state, market context, and data freshness are explicit.
- Upcoming, live, completed, provider-error, and no-game states share the Sheet
  grammar without fabricating data.
- Actions remain reachable on pointer, keyboard, and compact layouts.

### Stat Analysis

- Filters, refresh, matchup results, conditional context, charts, and game logs
  follow one information hierarchy.
- The detail panel and backdrop have predictable open, close, focus, and Escape
  behavior.
- Numeric facts use the monospaced figure treatment and preserve their labels at
  compact widths.

### Bet Builder

- Game, market, wager, prop, parlay, round-robin, and import workflows use one
  builder rather than duplicated legacy implementations.
- Mode controls are keyboard reachable and the recorded-position action remains
  visually dominant.
- The slip stays in document flow, collapses cleanly, and never duplicates its
  summary.

### Authentication

- Login and registration use the same auth sheet, field, validation, and action
  vocabulary.
- Every server-side field error is associated by `aria-describedby`; invalid
  controls expose `aria-invalid`.
- Password guidance and private-ledger positioning are accurate.

### Public trust surface

- Home, methodology, responsible gambling, privacy, terms, data sources, and
  about pages are deliberately public and linked through accessible breadcrumbs.
- Copy does not claim proven profitability, professional advice, fabricated
  accuracy, or fabricated usage.
- Legal pages are product copy pending jurisdiction-specific legal review before
  a hosted public launch.

### HTTP errors

- 400, 401, 403, 404, 405, 429, 500, 502, 503, and 505 share one safe Sheet view.
- Public details never echo exception/provider payloads. Every response displays
  and returns the same request reference.
- JSON clients receive `ApiErrorV1`; browser clients receive accessible HTML.
- Recovery actions reflect authentication state and work at 320px.

### Shared shell

- One masthead and primary navigation serve every viewport.
- Current navigation is marked by text weight/contrast and `aria-current`, never
  color alone.
- Workflow navigation appears only on workflow pages.
- Toasts and modals are overlay layers; the document-flow elevation budget is
  reserved for the slip.

## Release sign-off

The release gate is PASS only when all of the following are green:

- template/style/icon/framework contract tests;
- Playwright functional and logout-isolation tests;
- Playwright serious/critical Axe audit;
- Playwright responsive overflow checks at desktop, 412px, and 320px;
- reviewed desktop/mobile visual snapshots.

Record failures as test output or a tracked debt item with an owner and exit
condition; do not keep a second unchecked copy of the same backlog here.
