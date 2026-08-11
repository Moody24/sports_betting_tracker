# UI V1 Baseline Checklist

This document is the visual QA baseline for the current Sportsbook UI pass.
Use it before every release to keep cohesion and interaction quality stable.

> **Migration status (2026-08-10).** The Sheet migration replaces the card-based
> vocabulary this checklist is written in. Sections below still describing
> "KPI cards", "chart cards", "matchup cards", or "strong-play cards" describe
> the **pre-migration** UI and are superseded page by page as each phase lands.
>
> Updating the affected section is an **exit criterion of every phase** — see
> `docs/superpowers/sheet-migration-ledger.md`. Do not audit a migrated page
> against a section still marked pre-migration; that would fail a page for
> correctly removing what the migration set out to remove.
>
> Legend: **[MIGRATED]** current · **[PRE-MIGRATION]** superseded when its phase lands.

## Global Gates (must pass first)

1. Inline `style` attributes in templates may set **CSS custom properties only**
   (e.g. `style="--pace: 62%"`); every real declaration lives in `theme.css`.
   Server-computed numbers — a pace bar, a distribution's interquartile range —
   cannot be classed without quantising them into hundreds of buckets, so this
   is the one sanctioned use. Enforced by `tests/test_template_inline_styles.py`,
   which replaces the old honour-system grep.
2. Primary actions use consistent hierarchy (`btn-primary` for primary, toolbar secondary classes for non-primary).
3. Keyboard focus ring is visible on interactive controls.
4. Mobile layout remains usable at <= 575px width.
5. Reduced-motion users are respected via `prefers-reduced-motion`.

Quick checks:

```bash
SECRET_KEY=test python -m unittest tests.test_template_inline_styles
```

Expected result: OK. To eyeball what is currently set inline:

```bash
rg -n 'style="' app/templates
```

Every match must be custom properties only.

## Page Checklist

### Dashboard

1. KPI cards read clearly with consistent label/value hierarchy.
2. Chart cards have stable title/action spacing.
3. Donut legend dots and values are aligned and readable.
4. "Top Plays" and "Best Play Of The Day" action rows are consistent.
5. Recent bets table remains readable on mobile (no clipped labels).

### Bet Builder

1. Mode tabs are clearly distinguishable and keyboard reachable.
2. Section labels ("Game", "Market", "Wager", "Advanced") are consistent across tabs.
3. Primary CTA ("Record Bet/Prop/Submit") remains visually dominant.
4. Parlay split layout collapses cleanly on mobile.
5. OCR section spacing and parsed-fields panel remain coherent.

### Prop Analysis

1. KPI strip and filters feel balanced (no crowding).
2. Strong-play cards and main table share spacing rhythm.
3. Refresh/loading states are visible without layout jump.
4. Table actions (Bet/Parlay) keep consistent size and alignment.
5. Player detail modal content is readable and grouped logically.

### Stat Analysis

1. KPI row, filter toolbar, and refresh controls are cohesive.
2. Matchup cards maintain clear away/home separation.
3. Side panel "Case" values remain readable and tone-coded.
4. Stat toggle, charts, and game-log sections maintain hierarchy.
5. Panel open/close and backdrop behavior are keyboard-safe and predictable.

### NBA Today

1. Top toolbar uses consistent action hierarchy.
2. Active/completed/upcoming card styles remain from one visual system.
3. Live badges, score blocks, and quick actions align consistently.
4. Loading skeleton and error states are clear and non-jarring.
5. Mobile action stack remains tap-friendly.

### My Bets

1. Pending alert bar has consistent emphasis and spacing.
2. KPI strip coloring and typography remain token-driven.
3. Bet rows maintain clear separation between details and meta.
4. Live tracker card typography/progress remains readable.
5. Parlay expand/collapse icon and leg state remain intuitive.

### Auth + Shared Shell — **[MIGRATED, Phase 0]**

The sidebar was removed in Phase 0 and replaced by a horizontal masthead;
`tests/e2e/responsive.spec.ts` now asserts the same nav component at every
width, with no drawer, toggle, or overlay. The previous item 1 required
"sidebar, topbar, and user menu typography are consistent" and is void.

1. Masthead is byte-identical on every page: nameplate, rule, section row.
2. The active section is marked by **weight and contrast only** — no pill, no
   fill, no edge rail — and carries `aria-current="page"`.
3. Nav is reachable at every width down to 320px with no horizontal overflow.
   (Seven links at 320px currently fit but are not *designed*; see the ledger.)
4. The flow strip appears on exactly the four workflow pages, rendered as a
   ruled jump line — not tiles, not a tab bar.
5. Colophon and secondary text are readable but de-emphasised.
6. All dropdown/toolbar actions have sufficient tap-target size.
7. Auth brand subtitle spacing is consistent on login/register.
8. Home hero headline/subtitle hierarchy remains strong.

## Sign-off Record (per release)

Use this template for each release:

```text
Release:
Reviewer:
Date:

Global Gates: PASS / FAIL
Dashboard: PASS / FAIL
Bet Builder: PASS / FAIL
Prop Analysis: PASS / FAIL
Stat Analysis: PASS / FAIL
NBA Today: PASS / FAIL
My Bets: PASS / FAIL
Auth + Shared Shell: PASS / FAIL

Open Issues:
- ...

Decision:
- Ship / Hold
```
