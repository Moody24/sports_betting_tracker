# Claude handoff — 2026-08-11 — the Sheet migration

Durable save point for the UI design-system migration on branch
`sheet-phase-0`. Phases 0, 0.5 and 1 are complete and committed. Phase 2
(Prop Analysis) has not started.

## Repository state

Branch `sheet-phase-0`, working tree clean, nothing pushed.

| Commit | What |
|---|---|
| `5125b8a` | Phase 0 — Sheet design system: dark ground, masthead, zero radius |
| `cd91bdd` | Phase 0.5 — migration agents, ledger, rubric correction |
| `c5dff6c` | The Sheet grammar layer — frozen union class contract |
| `f093f65` | Phase 1 — My Bets becomes the Position Log |
| `4a3db7f` | closing_odds / closing_line columns + CLV |
| `53f6d9b` | noindex on every private surface, fail closed |

Apply migrations before running anything: `flask db upgrade` (head is
`e9602669917f`).

Gates at time of writing: **1232 Python · 32 Playwright · 0 axe
serious/critical · ruff + bandit clean · no overflow at 1440/412/320.**

## The design direction, in one paragraph

"The Sheet": a warm-graphite ground read in the dark (NBA tips off 7–10:30pm
ET), a horizontal newspaper masthead instead of a sidebar, hairline-separated
rows that are never boxed, zero border radius, and no chromatic accent —
colour appears only where it encodes direction. Grounded in sports agate
typography and the Daily Racing Form, not in dashboard convention. Five prior
directions were rejected; the recorded lesson is that **a recolour, or a
rename, over unchanged structure is always detected.** Authority for tokens is
`app/static/css/theme.css`; the rubric is
`.claude/skills/ui-design-rubric/SKILL.md`.

## Where the truth lives

- **`docs/superpowers/sheet-migration-ledger.md`** — the SSOT for this
  migration: file ownership, phase order, the frozen class contract, contract
  amendments, data gaps, and open user decisions. Read it before Phase 2.
- **`.claude/agents/sheet-*.md`** — seven role-based agents. Decomposed by
  role rather than phase because every phase-agent would have wanted to write
  `theme.css`, which is where the conflicts actually live.
- **`docs/architecture/system-contract.md`** — architecture SSOT, unchanged.

## What Phase 0.5 fixed, and why it existed

A cold adversarial review found four defects that would each have failed in
Phase 1. Every claim was verified against source before acting:

1. **Phase order was backwards.** Nine structural components exist in My Bets
   and zero in Prop Analysis. A capability inventory across all six page
   templates: My Bets 12/16, Prop Analysis 9/16, union 16 — **no page is a
   superset**. The contract is now frozen from the union, and Phases 1 and 2
   swapped.
2. **The specs pinned markup nobody could fix.** `tests/e2e/**` belonged to
   the gate-runner, which is forbidden to edit tests. Spec selectors now
   resolve through `data-testid` and belong to the migrator that renames the
   markup.
3. **Four shared files had no owner** — `_macros.html`, `_icons.html`,
   `_workflow_nav.html`, `script.js`. All now belong to the css-steward.
4. **The rubric prescribed a rejected direction.** It still specified cobalt
   `#5872E8`, Inter, and 6/10/999px radii — rejection #4 — while being cited
   to the design critic as authoritative.

Also in 0.5: `icon()` became a Jinja global (twelve templates called it, one
imported it — the rest relied on a Jinja scoping accident); the flow strip was
redesigned out of three simultaneous blacklist hits; and `tools/shoot.mjs`
was committed, because the rubric referenced a screenshot helper that existed
only in a scratchpad.

## Phase 1 — what shipped

My Bets is the Position Log: notice → control bar → slip → column band →
hairline rows → pager. Its signature display is the **pace axis**, which
carries three facts on one strip (fill = where the player is, tick = the line,
colour = the verdict). `bets_list.js` now returns a semantic `tone`, so the
trend badge and the bar cannot disagree by construction — they did once.

All seven definition-of-done fields are verified present on live rows, and
every control (filters, search, export, add, check-now, grade, parlay toggle,
delete) was re-verified.

**Five contract amendments**, all generic rather than page-specific:
responsive `--cols` collapse; `.is-optional` + `.is-compact-only`;
`.row-full`; `.row-figure.is-up/.is-down`; control-bar form sizing. Rationale
sits beside each in `theme.css` and in the ledger.

**Three bugs only rendering could have found** — each worth remembering
because reasoning would not have caught any of them:

- `minmax(0, 1fr)` constrains the *track*; a grid item still defaults to
  `min-width: auto` and pushed the document sideways at 320px.
- A media query adds no specificity, so a base rule placed *after* it wins at
  every width — the collapsed figures silently never appeared.
- A status tag received free text (the server's error sentence) inside a
  `nowrap` pill. A tag is a closed vocabulary of short labels; that was a
  design error, not a CSS one.

## CLV and the crawler register

`closing_odds` and `closing_line` now exist on `Bet`, with `clv_pct` and
`line_move`. **Nothing populates them yet** — that is the next real decision
(see below). `clv_pct` returns `None`, never `0.0`, and the Position Log
renders CLV only when a close exists.

Every private surface renders `noindex, nofollow` via an allowlist that fails
closed. Adding a route does not make it indexable;
`tests/test_crawler_register.py` asserts registers from rendered responses and
fails if the allowlist widens.

## Next: Phase 2 — Prop Analysis becomes the Board

This is where `.pp` — the past-performance strip, the direction's signature
data display — finally has real data, because every row is a tonight's-slate
player prop and `PlayerGameLog` has the game-by-game history.

Read first: the ledger's frozen contract, then
`app/templates/bets/nba_analysis.html` (568 lines, of which 253 are page JS
inline in `{% block scripts %}`, not a file in `app/static/js/`).

Known specifics for that page:
- It has a modal and a toast container; My Bets has neither. Overlays are a
  separate elevation layer and do not count against "one raised surface".
- Its KPI cards are Bootstrap-grid boxes with big numerals in three different
  accent colours — blacklist #7, #8, #15 and "one accent" simultaneously.
- `data-testid="analysis-summary"`, `"analysis-table"` and
  `"player-detail-table"` already exist; keep them.
- The `.pp` component cannot branch on the sign of a custom property in CSS,
  so the template sets `--pp-ink` to `var(--win)`/`var(--loss)`/`var(--push)`.

Then Phase 3 (NBA Today + Dashboard, **serial**), Phase 4 (Stat Analysis +
Bet Builder, **serial**), Phase 4.5 (auth, errors, import), Phase 5 (public
surface and the rest of SEO).

## Open decisions — yours, not mine

1. **How do closing prices get captured?** The columns exist and are empty.
   The realistic options are a scheduled job that snapshots prices near tip-off
   (the scheduler already has 22 jobs and an event-relative prop-close capture
   pattern to copy), or manual entry on the bet form. Until one exists, CLV is
   dark everywhere.
2. **Should Home become a real public landing page?** It is the only indexable
   route. If it stays effectively private, Phase 5 shrinks to almost nothing.
3. **Do the tier scales (`value` / `slight` / `avoid`) keep amber, or move to
   win-green?** Deliberately not decided globally.

## Debt deliberately not carried along

- **124 orphaned CSS selectors** of 650 (word-boundary scan). Earlier figures
  of 132 and 158 were both wrong. A large share are `bb-*` classes belonging
  to `app/static/js/bet_builder.js` (1,349 lines), which **no template loads**
  — `form.html` loads `unified_bet_builder.js`. Retiring that dead file first
  shrinks the backlog cheaply.
- **Twelve blank-rendering `bi-*` icon references** across `betslip.js`,
  `unified_bet_builder.js`, and `bet_builder.js`. No Bootstrap Icons CSS or
  font is loaded anywhere, so they render as empty elements. Ratcheted by
  `tests/test_icon_macro.py` so the count cannot grow.
- **No mobile navigation design.** The sidebar went in Phase 0 and the
  responsive spec now asserts one nav at every width. Seven masthead links at
  320px fit inside a scroll container; that is not the same as designed.
- **No a11y remediation owner.** axe is a gate, but the gate-runner is
  forbidden to fix what it finds.
- `design-mock/index.html` matches the shipped palette but uses **different
  token names** (`--paper`/`--ink`/`--rule` vs `--bg`/`--text-main`/
  `--border-subtle`) and opaque rather than alpha hairlines.
- **`responsive.spec.ts` still pins `.kpi-card` width > 120px** on purpose.
  It is a real assertion about card shape, not plumbing; Phase 3 must
  *replace* it when the KPI row becomes a ledger band, not repoint it.

## Standing constraints

- Tests are **unittest**, never pytest:
  `SECRET_KEY=test python -m unittest discover -s tests`. Run suites in the
  **foreground** — backgrounded subagent runs die on this setup.
- All date/time logic uses ET (`ZoneInfo("America/New_York")`).
- Never edit `.env` or `app/ml_models/*.json`.
- **Never add `Co-Authored-By` to a commit.** Commit only when asked.
- The repo is **public**. Check before pushing.
- Inline `style` in templates may set CSS custom properties only.
