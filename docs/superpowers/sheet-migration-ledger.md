# Sheet migration ledger

Shared state for the Sheet UI migration agents. `sheet-orchestrator` is the
only writer; every other agent reads it. If two agents disagree about who owns
a file or what a class means, this file settles it.

> **Revised 2026-08-10 after a cold adversarial review.** The first version of
> this plan had four defects that all fired in Phase 1. What follows is the
> corrected plan; the corrections are recorded inline so they are not
> re-litigated. See "What the review changed" at the foot.

## File ownership — single writer per path

| Path | Sole writer |
|---|---|
| `app/static/css/theme.css` | `sheet-css-steward` |
| `app/templates/base.html` | `sheet-css-steward` |
| `app/templates/_macros.html`, `_icons.html` | `sheet-css-steward` |
| `app/templates/bets/_workflow_nav.html` | `sheet-css-steward` |
| `app/static/js/script.js` (global behaviour) | `sheet-css-steward` |
| `app/templates/_head.html` | `sheet-seo-steward` (extracted `53f6d9b`) |
| `app/templates/<one page>.html` | `sheet-page-migrator`, one page per dispatch |
| that page's JS **and** its `tests/e2e` selectors | the same `sheet-page-migrator` |
| snapshot **regeneration** only | `sheet-gate-runner` |
| `docs/**`, this ledger | `sheet-orchestrator` |

Two corrections to the original table, both load-bearing:

- **The shared Jinja layer had no owner.** `_macros.html` (189 lines) is
  imported by four page templates across three phases and emits the exact badge
  vocabulary the Sheet replaces; `_icons.html`, `_workflow_nav.html`, and the
  global `script.js` were likewise unowned. They now belong to the steward,
  because they *are* the component layer in Jinja form.
- **Spec selector updates belong to the migrator, not the gate-runner.** The
  gate-runner is forbidden to edit tests to make them pass — correctly — so
  assigning it `tests/e2e/**` meant nobody could update a selector the
  migration renamed. A migrator that renames markup now updates the spec in the
  same dispatch. The gate-runner keeps snapshot *regeneration* only.

**Phases 3 and 4 are SERIAL, not parallel.** Their page pairs both need
`_macros.html` (Phase 3) and `_workflow_nav.html` (Phase 4), so the file sets
are not disjoint and the parallel-dispatch precondition is not met.

## Dispatch order within a phase

1. `sheet-css-steward` freezes the class contract → recorded below.
2. `sheet-page-migrator`, per surface — template, its JS, and its spec selectors.
3. `sheet-design-critic` grades rendered screenshots → `SHIP` / `REWORK`.
4. `sheet-contract-auditor` checks written contracts → `PASS` / `FAIL`.
5. `sheet-gate-runner` runs suites, regenerates snapshots exactly once.
6. Orchestrator records the result and **updates the affected section of
   `docs/ui_v1_baseline.md`** — an exit criterion for every phase, not an afterthought.

## Phase status

| Phase | Surfaces | State | Notes |
|---|---|---|---|
| 0 | Shell, tokens, masthead | **Complete** (`5125b8a`) | Dark ground, zero radius, zero chromatic accent. |
| 0.5 | Ownership, contracts, shared-layer debt | **Complete** (`cd91bdd`) | Checklist below, all items closed. |
| 1 | **My Bets → the Position Log** | **Complete** | Grammar shipped end to end; pace axis is its signature display. Five contract amendments, below. |
| 2 | **Prop Analysis → the Board** | Not started | Consumes Phase 1's vocabulary; adds the dense-table variant and the modal. |
| 3 | NBA Today + Dashboard | Not started | **Serial.** Dashboard's KPI row becomes the ledger band. |
| 4 | Stat Analysis + Bet Builder | Not started | **Serial.** Need new thinking, not new paint. |
| 4.5 | Auth, errors, import | Not started | Added — half the template surface the original plan did not see. |
| 5 | Public surface + SEO | Not started | Metadata, breadcrumb contract, robots.txt, sitemap.xml, legal pages. |

### Why Phase 1 and 2 swapped

The original plan put Prop Analysis first as "the hardest page, which ships the
components the rest reuse". That premise is false. Counted directly, **nine
structural components appear in My Bets and zero in Prop Analysis**:

| Component | `bets/list.html` | `bets/nba_analysis.html` |
|---|---|---|
| Grouped rows w/ collapsible header (parlays) | 4 | 0 |
| Live-progress row (7 required fields) | 1 | 0 |
| In-row disclosure (`<details>`) | 1 | 0 |
| Pagination | 9 | 0 |
| Dropdown action menu | 1 | 0 |
| POST form + CSRF action cluster | 6 | 0 |
| Page-level alert bar | 1 | 0 |
| Ledger band (`kpi-strip`/`kpi-cell`) | 7 | 0 |
| Chip vocabulary (`bet-chip`) | 5 | 0 |

Prop Analysis has a modal and a toast container that My Bets lacks, so the two
pages **overlap**; neither contains the other. Freezing a contract from Prop
Analysis guaranteed the Phase 2 migrator would hit missing components, stop,
and unfreeze the contract mid-phase — serialising exactly what the ownership
model exists to parallelise.

My Bets also carries the hardest written contract (the seven live-progress
fields, `system-contract.md`), the most existing coverage, and working
prototypes of both the ledger band and the chip that the original plan proposed
to *invent* in Phase 1 and again in Phase 3.

**The class contract is frozen from the UNION of all six page templates**, via a
component-inventory pass, not from whichever page goes first.

## Phase 0.5 checklist

- [x] Rewrite `ui-design-rubric/SKILL.md` "Working tokens" — it still specified
      cobalt `#5872E8`, Inter Variable, and 6/10/999px radii, i.e. **rejected
      direction 4**, while being cited to agents as authoritative. Now records
      the shipped Sheet tokens; rejection list corrected from three to five.
- [x] Make the grading helper real: `tools/shoot.mjs` (1440/412/320, overflow +
      axe, non-zero exit). The rubric referenced a `shoot*.mjs` that existed
      only in a scratchpad. Grading width corrected 390 → **412**, the only
      mobile width with a Playwright baseline.
- [x] Register `icon()` as a Jinja global. Twelve templates call it and one
      imports it; the rest resolved it only because `base.html`'s top-level
      import leaked into their block context. Extracting `_head.html` would
      have degraded every icon in the app to an HTML comment — invisible, and
      passing every Python test. Guarded by `tests/test_icon_macro.py`.
- [x] Redesign the flow strip. It was four equal bordered icon tiles with
      `transition: all` — blacklist #6, #7, and #11 — while being a *mandated*
      grammar part, so the critic would have had to REWORK the first page it
      graded. Now a ruled jump line; active by weight and contrast only.
- [x] Add `--font-display` / `--font-mono` tokens; stop hardcoding stacks.
- [x] Remove dead live-tracker code in `bets_list.js` that queried a Bootstrap
      icon class never rendered. Ratchet test added for the remaining 12
      blank-rendering `bi-*` references in three other JS files.
- [x] Replace brittle spec selectors with `data-testid` hooks. Seven page
      readiness/plumbing selectors across three spec files now resolve through
      `data-testid`, so renaming markup no longer breaks a spec. **One pin was
      deliberately left**: `responsive.spec.ts` asserting `.kpi-card` width
      > 120px is a real assertion about card shape, not plumbing — Phase 3 must
      *replace* it when the KPI row becomes a ledger band, not repoint it.
- [x] Update the stale sections of `docs/ui_v1_baseline.md`. Added a migration
      banner and a `[MIGRATED]`/`[PRE-MIGRATION]` legend; rewrote the Auth +
      Shared Shell section, which still required the sidebar Phase 0 deleted.
- [x] Component-inventory pass across all six page templates; union frozen
      at 16 capabilities. See the contract table above.
- [x] Commit Phase 0 before Phase 1 opens. Landed as `5125b8a` + `cd91bdd`.

## Frozen class contracts

### The union contract — frozen 2026-08-10

Derived from a capability inventory of all six page templates, not from one
page. Coverage: My Bets 12/16, Prop Analysis 9/16, Stat Analysis 8/16, NBA
Today 6/16, Dashboard 5/16, Bet Builder 4/16. **Union = 16.**

Names are by role in the grammar, never by page — that is what makes them
reusable. A page composes a subset; a page that needs a *new* part must stop
and ask, because inventing structural parts per page is what makes an app feel
incoherent.

| Grammar part | Classes | Notes |
|---|---|---|
| Sheet head | `.sheet-head` `.sheet-title` `.sheet-dateline` | Dateline is mono, right-aligned: counts, freshness, staleness |
| Control bar | `.control-bar` `.chip` `.chip.is-active` `.chip-clear` | Closed vocabulary: unset `+`, active `×`, Clear-N only when active |
| Column band | `.band` `.band-cell` `.band-cell.is-num` | Headers share their column's alignment |
| Row | `.row-line` `.row-lead` `.row-sub` `.row-figure` `.row-figure.is-lead` `.row-meta` `.row-actions` | Hairline-separated, never boxed, never zebra. Exactly one `.is-lead` per row |
| Grouped rows | `.row-group` `.row-group-head` `.row-group-body` | Parlays; the head is the toggle |
| In-row disclosure | `.row-more` `.row-detail` | Level 2 of 2. Label carries scent, never "More" |
| The slip | `.slip` `.slip-head` `.slip-grid` `.slip-fact` | One per screen **in document flow**; overlays exempt |
| Pace axis | `.pace` `.pace-fill` + `.pace-good\|warn\|bad\|neutral` `.pace-mark` | Fill colour must agree with the trend verdict |
| Signature display | `.pp` `.pp-cell` `.fan` `.fan-iqr` `.fan-tick` | **Phase 1 ship criterion — see below** |
| Status | `.tag` + `.tag-win\|loss\|push\|live\|pending` | Word labels, tinted, non-interactive |
| States | `.state-empty` `.state-loading` `.state-error` | "No data yet" must not look like "filtered to zero" |
| Pagination | `.pager` `.pager-item` | |
| Notice | `.notice` | Page-level alert bar |
| Actions | `.act` `.act-menu` | Visible on hover **and** on keyboard focus |

Overlays (`.modal*`, `.toast*`) stay on the existing shell classes and are not
part of the flow contract.

**Phase 1's ship criterion is `.pp`** — a dense repeating past-performance unit
in the Daily Racing Form sense: last N games, the line, the result against it,
as one scannable block per row. This is the test of whether the direction has
an argument or only a vocabulary. Hairlines, a column band, and a slip could be
on any dashboard; this could not. If it cannot be made to work, that is a
finding about the direction and must surface now, not in Phase 5.

### Contract amendments from Phase 1

The frozen contract survived contact largely intact, but five gaps only a real
page could expose. All are generic, not page-specific:

| Amendment | Why it was needed |
|---|---|
| `--cols` collapses to two columns below 48rem | The contract had no responsive rule at all, so any sheet with more than two columns overflowed at 320px. |
| `.is-optional` / `.is-compact-only` | Columns are *dropped* at narrow widths, not squeezed. The pair is the bargain: what the band drops, the meta line picks up, so a figure is never simply unreachable. |
| `.row-full` | A row is a grid, so anything belonging to the whole row (pace axis, disclosure) was trapped in column one. |
| `.row-figure.is-up` / `.is-down` | Direction on a *figure* is colour only. The tag classes were the nearest existing thing and painted every P/L as a filled chip — a row of boxes, which is what this grammar exists to remove. |
| `.control-bar label` sizing | A control bar may carry real form controls, not only chips. The pre-migration `width:100%` rules turned the bar into a tall stack of full-width inputs. |

Three bugs found by rendering rather than by reasoning, each worth remembering:
`minmax(0,1fr)` on the track is not enough without `min-width:0` on the grid
*item*; a media query adds no specificity, so a base rule placed after it wins
at every width; and a status tag must never receive free text — the server's
error sentence inside a `nowrap` pill was what pushed the document sideways.

## Elevation rule (restated)

"Exactly one raised surface per screen" was unimplementable as written:
`base.html` renders a global toast stack on any page with a flashed message,
and Prop Analysis independently ships a modal plus a second toast container —
four raised surfaces on the page the rule was being invented on. Corrected:

> **At most one raised surface in the document flow (the slip). Overlays —
> modal, toast — are a separate layer with their own elevation rule and do not
> count against it.**

## Verdict log

*(Appended per dispatch: date, agent, surface, verdict, one-line reason.)*

- 2026-08-10 · cold adversarial review · the plan · **RETHINK** · four blockers
  all firing in Phase 1; ordering inverted; Phase 0.5 and 4.5 inserted.

## Open decisions owned by the user

- **How do closing prices get captured?** `closing_odds` / `closing_line` now
  exist on `Bet` with `clv_pct` and `line_move`, but nothing populates them, so
  CLV is dark on every row. Options: a scheduled snapshot near tip-off (the
  scheduler already has an event-relative prop-close capture pattern to copy),
  or manual entry on the bet form.
- Whether Home becomes a real public landing page while the app is local-only.
  If it stays private, Phase 5 shrinks to the `noindex` work alone.
- Whether the tier scales (`value` / `slight` / `avoid`) keep amber for the top
  tier or move to win-green. Decided per page, deliberately not globally.

## Known debt, explicitly not riding along

- **124 orphaned selectors** of 650 total (word-boundary scan across
  `app/templates`, `app/static/js`, `tests/**`). The earlier figures of 132 and
  158 were both wrong. A large share are `bb-*` classes belonging to
  `app/static/js/bet_builder.js` (1,349 lines), which **no template loads** —
  `form.html` loads `unified_bet_builder.js`. Retiring that file first would
  shrink the backlog cheaply.
- Twelve blank-rendering `bi-*` icon references remain in `betslip.js` (6),
  `unified_bet_builder.js` (3), and `bet_builder.js` (3). No Bootstrap Icons
  CSS or font is loaded anywhere. Ratcheted by `tests/test_icon_macro.py`.
- **No mobile navigation design.** The sidebar was deleted in Phase 0 and the
  responsive spec now asserts one nav at every width. That is seven masthead
  links at 320px. It fits today; it is not designed, and no phase owns it.
- **No a11y remediation owner.** axe is a gate but the gate-runner is forbidden
  to fix what it finds.
- `design-mock/index.html` palette is now in sync with `theme.css`, but the two
  use **different token names** (`--paper`/`--ink`/`--rule` vs
  `--bg`/`--text-main`/`--border-subtle`) and different hairline mechanisms
  (opaque hex vs alpha). The critic grades the mock; the user sees the app.
- **The masthead and the flow strip are now visually adjacent horizontal link
  runs** and risk reading as two navs. Grade this explicitly on the first
  migrated page.

## What the review changed

Four blockers, all firing in Phase 1: the class contract could not be frozen
from Prop Analysis; the specs pinned markup nobody was permitted to update; four
shared files had no owner, which killed the parallelism in the two phases
designed for it; and the rubric declared authoritative to the design critic
still prescribed a rejected cobalt direction.

The review's design critique is recorded separately because it is not yet
resolved: **strip the newspaper vocabulary and what remains is a competent dark
data table** — arguably rejection 3 under new nouns. Every information-design
rule in the plan (natural-frequency probabilities, devigged fair lines,
stake-relative dollars, CLV, two-level disclosure) is separable from the
newsprint layer, which is evidence the newsprint layer is decoration. The
counter is that the mock already prototypes two genuinely subject-specific
displays — the simulated-outcome fan and the three-fact pace axis — but the
*plan* commits to shipping neither.

**Therefore Phase 1's ship criterion is a signature data display**, not merely
hairline discipline: a dense repeating past-performance unit per row in the
Daily Racing Form sense. If the direction cannot produce one distinctive
display, that must be discovered in Phase 1, not Phase 5.
