# Phase 2 — UI Rebuild: De-Bootstrap + ML Surfaces (design)

**Date:** 2026-07-17. **Status:** approved design, pre-plan.
**Depends on:** frontend-overhaul (June 2026 amber system), Plan C increments
1–2 (both flags live: `USE_DISTRIBUTIONAL_MODEL`, `USE_SCENARIO_SIGNAL`).

## Goal

Three increments, one spec:
- **Increment A — de-Bootstrap foundation:** remove the Bootstrap CSS/icons
  CDN dependency entirely; the app renders pixel-equivalent from self-hosted
  fonts + a single `theme.css`. Zero external requests. Visual parity is the
  gate — nothing moves, nothing new appears.
- **Increment B — ML surfaces:** ship the product face of the Plan B/C
  platform: scenario-split evidence on pick cards, distributional provenance
  + quantile strip on pick cards, and a model-health dashboard (the October
  operator view).
- **Increment C — the beauty pass:** a deliberate, user-judged polish
  increment over every screen. Parity (A) is a regression gate, NOT the
  ambition; C is the ambition. Driven by the frontend-design skill; "wow"
  is accepted per-screen by the user, not by checklist.

## Verified starting state (audited 2026-07-17)

- 15 templates / 3,457 lines. Layout is ALREADY custom (app-shell + sidebar
  in `base.html`); identity is already the amber system (`theme.css`, 3,316
  lines, all colors from `:root` custom properties).
- Bootstrap coupling is shallow: ~20 distinct classes in real use (btn 83,
  card/card-soft 77, row 40, badge 40, col-* ~45, form-control 12, plus
  nav-link/table/alert/spinner); 122 `bi-*` icon-font uses; **zero**
  `bootstrap.*` JS API calls in custom JS; only 3 `data-bs-toggle` + 1 each
  `data-bs-target`/`dismiss`/`theme` attributes.
- External requests today: Google Fonts (Syne/Outfit/JetBrains Mono),
  Bootstrap 5.3.3 CSS CDN, Bootstrap Icons CDN.
- Design-system law (kept): amber tokens on `#0D0B08`, the three fonts, no
  glow/shadow effects, single CSS file, no build step, vanilla JS only.
  This rebuild REPLACES the "Bootstrap 5.3 dark" clause of that law; every
  other clause stands.

## Decisions (made during brainstorming — do not re-litigate)

1. **Scope:** de-Bootstrap AND all three ML surfaces (user choice).
2. **CSS strategy:** hand-rolled, no build step, everything stays in
   `theme.css`. No Tailwind, no Node, no new CSS files.
3. **Migration mechanics — own the vocabulary:** reimplement the ~20
   Bootstrap class names IN USE under the same names in `theme.css`
   (CSS-grid `row`/`col-*` with the definition-of-done breakpoints
   576/768/992, `btn` + 5 used variants, `card`, `badge`, form controls,
   `table`, `alert`, `nav-link`, `spinner-border`, plus ONLY the ~40 utility
   classes actually used — audit step enumerates them; YAGNI). Template
   churn is confined to icons and the 3 JS behaviors.
4. **Icons:** Jinja macro `{{ icon('name') }}` in `_macros.html` rendering
   inline SVG (16×16, `currentColor`) from a curated dict of the ~30 glyphs
   in use, paths taken from MIT-licensed Bootstrap Icons. All 122 `bi-*`
   uses migrate; the icon CDN dies.
5. **Behaviors:** the 4 `data-bs-*` behavior uses are replaced by one small
   delegated-click vanilla-JS handler (~30 lines, in `script.js`). No
   Popper/bundle.
6. **Fonts self-hosted** (woff2, exact weights in use) in
   `app/static/fonts/` — the app becomes fully offline-capable.
7. **Sequencing:** A merges at visual parity BEFORE B starts (B never
   builds on Bootstrap); C runs last, over everything, as its own
   increment with per-screen user acceptance. A's parity gate is a
   regression control, not the product ambition — C is the ambition
   (user decision 2026-07-17: "I want wow, not clunky").

## Increment A — components

- `theme.css` gains a clearly-delimited "framework layer" section
  (grid → components → utilities), all styled from existing tokens.
- `_macros.html` gains the `icon()` macro + glyph dict.
- `base.html`: both Bootstrap CDN links deleted; Google Fonts links replaced
  by `@font-face`; `data-bs-theme` attribute dropped (no consumer remains).
- Templates: `bi-*` → `{{ icon(...) }}`; `data-bs-*` → `data-toggle`
  equivalents handled by the new handler.
- **Parity gate for A:** all 15 templates at the 3 breakpoints with no
  visual regression; definition-of-done control-regression checklist passes;
  browser network panel shows ZERO external requests; grep proves no `bi-`,
  no `data-bs-`, no bootstrap URL anywhere in templates.

## Increment B — components

**B1. Scenario evidence on pick cards.**
- Card chip beside the confidence tier: lean direction + agreement strength
  (amber = over, muted red `#8B3A3A` = under) + match count. Renders ONLY
  when `scenario_agreement is not None` (flag-off cards unchanged).
- Drill-in: native `<details>` row listing matched conditions — one line per
  matched split: dimension label, bucket, shrunk mean vs line, n.
- **Additive backend hook:** `agreement_score_details(player_id, stat, line,
  context, splits=None)` in `scenario_engine.py` returning
  `(score, matches, details_list)` where `details_list` =
  `[{dim, bucket, n, shrunk_mean, direction}]`. `agreement_score` keeps its
  exact signature/behavior (delegates or stays as-is); the scan cache's
  `splits=` prefetch param is honored. `score_prop` carries `details_list`
  as `scenario_details` in the score dict (None when no signal).

**B2. Distributional provenance on pick cards.**
- Provenance badge from the existing `projection_source` field: `DIST`
  (amber outline) vs `GAUSS` (muted) — shows which model priced the line.
- Quantile strip: inline SVG rendered at Jinja time (like the existing
  sparkline): the 10 predicted quantiles as a horizontal fan, book line as
  a tick. Rendered ONLY when the score dict carries `dist_quantiles`.
- **Additive backend hook:** the dist path already computes the full
  distribution; `_model_prob_over_details` → `score_prop` passes
  `quantile_values` through as `dist_quantiles` (None on the Gaussian
  path/flag-off). No new model calls.

**B3. Model-health dashboard** — new route `GET /models` (auth like other
pages), sidebar entry "Models", template `models.html`. Four render-time
sections from existing tables, no new state, no JS charts:
1. Model registry: per stat — active point/dist/calibrator `ModelMetadata`
   rows, training date, MAE + samples parsed from `metadata_json`.
2. Backtest history: `JobLog` rows `job_name='distributional_backtest'` →
   dist vs baseline ECE + verdict per run (parse the recorded message).
3. Pipeline health: latest run + status dot (existing ribbon idiom) for the
   key jobs: retrain, refresh_scenario_splits, game_day_coordinator,
   distributional_backtest.
4. Flags & freshness: live values of the two `USE_*` flags;
   `ScenarioContextPack.computed_at` with the same 7-day freshness rule the
   live builder uses.

## Increment C — components (the beauty pass)

All within the standing laws (no build step, vanilla JS, single theme.css,
amber tokens, three fonts). Modern-CSS-first; JS only where CSS cannot.

- **Motion system:** a tokenized motion scale in `:root` (2 durations, 2
  easings); page transitions via the native View Transitions API
  (progressive enhancement — no-op on unsupported browsers); staggered
  entrance animation for card grids (CSS only, animation-delay off
  `:nth-child`); hover-lift/press states on all interactive components;
  every animation gated behind `prefers-reduced-motion: reduce`.
- **KPI number tickers:** count-up on dashboard KPI values (tabular-nums
  JetBrains Mono, ~15 lines of vanilla JS, `IntersectionObserver`-triggered,
  reduced-motion → instant).
- **Component craft pass:** adopt shadcn-grade detail into our owned
  components — consistent radius + spacing scale as tokens, layered focus
  rings (`:focus-visible`), skeleton loading states for live-progress rows,
  designed empty states (icon + line + action) replacing bare text.
- **Showpiece treatment** for the two signature visuals: the quantile fan
  (gradient fill under the curve, line-tick with label, animated draw-in)
  and the scenario evidence drill-in (aligned columns, n-weight encoded as
  bar depth, smooth `<details>` expansion).
- **Acceptance:** per-screen before/after review with the user; a screen is
  done when the user says it wows, within the design-system laws. The
  frontend-design skill is REQUIRED at execution time for this increment.
- **Fallback recorded:** if C lands and does not satisfy, the React/shadcn
  conversation reopens explicitly (decisions transfer; no wasted work).

## Error handling

Degrade to absence, never break a page: every new card element renders
nothing when its field is None (which IS the flag-off state — flag-off pages
stay byte-identical); every dashboard section has an explicit empty state
("No backtests recorded") behind a defensive parse (bad/legacy
`metadata_json` or JobLog message → skip row, log once, page still 200).

## Testing

- Route/render tests (existing pattern): `/models` 200 + section markers,
  with seeded ModelMetadata/JobLog fixtures AND with empty DB (empty
  states); pick-card partials render chips when fields present, render
  NOTHING when None.
- Unit: `icon()` macro (known glyph → svg, unknown glyph → safe fallback +
  log); `agreement_score_details` (additive — existing `agreement_score`
  callers byte-unchanged; details rows match the score's matched splits);
  `dist_quantiles` pass-through (present on dist path, None on fallback).
- Greps as tests where cheap: no `bi-`/`data-bs-`/bootstrap-URL in
  templates after A.
- Manual gate per touched template: definition-of-done checklist (3
  breakpoints, live-progress row fields, control regression).
- Standard gates: full suite FOREGROUND with `set -o pipefail`, coverage
  ≥ 80%, ruff, bandit.

## Out of scope (recorded)

Any layout/visual-language change beyond parity (the amber system stands);
new chart libraries or build tooling; UI for copulas/CLV (later phases);
editing the definition-of-done skill (it survives as-is; its "Bootstrap"
references update to the owned vocabulary in a doc-only follow-up).

## Rollout

No flags needed: Increment A is parity (reviewable as "nothing changed");
Increment B's card elements self-gate on field presence (i.e. on the
existing `USE_*` flags), and `/models` is a new page with no existing
consumers. Each increment: branch → gates → whole-branch review → merge —
the same loop as Plan C.
