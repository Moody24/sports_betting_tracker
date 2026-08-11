---
name: ui-design-rubric
description: "INVOKE for ANY Edge Tracker UI/design work — new screens, mockups, component styling, or design review. Encodes the user's design taste (five rejected directions), the SHIPPED 'Sheet' tokens, the research-derived slop blacklist + craft checklist, the reference anchors (Linear/Stripe/Mercury/Polymarket/DK), and the mandatory build→screenshot→grade loop."
---

## The design bar (learned the hard way — five rejections)

The user's bar: "would a senior front-end dev at Linear/Stripe ship this?"
Rejected directions, never to be resampled:
1. **Amber/gold system** (June 2026) — "looks stupid," dated.
2. **Aurora kit** — violet-teal gradients, glow filters, glassmorphism,
   gradient text, radial atmosphere, cute chart captions → "AI slop."
3. **Cheap flat-minimal** — a dark table with default-feeling styling →
   "bland." Restraint WITHOUT craft is equally a failure.
4. **Cobalt/graphite reskin** (2026-08-09) — the existing card layout in a new
   colorway → "just the same design with a different colorway."
5. **Near-black + phosphor-green terminal** (2026-08-10) — "still very AI slop
   — green highlights, uniform shapes, round square edges, side highlight on
   the side navbar."
Also: "normal-looking" is not enough — the design must earn distinctiveness
through information design, signature data displays, and voice, never
through decoration. Serve BOTH square (novice) and sharp (pro) bettors:
plain-language explanations available everywhere, density preserved.

**Renaming is the same failure as recolouring.** Rejections 4 and 5 were both
detected instantly because structure was unchanged. Calling a header a
"masthead" and a footer a "colophon" is not a design; if stripping the
vocabulary leaves a competent dark data table, that is rejection 3 wearing a
new noun. The direction must ship at least one **signature data display** that
could not appear in any other product.

## Reference anchors (user-named)

- **Linear** — skeleton: spacing, typography, keyboard flow, luminance-ladder
  dark elevation (no shadows), one accent.
- **Stripe Dashboard + Mercury** — data surfaces: table discipline, filter
  chips, closed badge vocabulary, tabular numerals on all money/data.
- **Polymarket** — probability-first rows; markets as browsable content.
- **DraftKings/FanDuel** — betting furniture: odds chips, slips, prop cards.

## SHIPPED tokens — "the Sheet" (accepted 2026-08-10, live in theme.css)

Superseded the cobalt "Board" mock of 2026-07-18. That mock's palette —
cool graphite `#0B0C0E…`, accent cobalt `#5872E8`, Inter Variable, 6/10/999px
radii — **is rejection 4 and must never be resampled.** Anything below that
disagrees with `app/static/css/theme.css` loses to `theme.css`.

Warm graphite ladder, elevation by luminance only:
`--desk #0E0D0B → --bg #171511 → --bg-soft #201D18 → --bg-elev #262219`.
Ink `--text-main #E8E4DA` / `--text-muted #A29B8D` / `--text-dim #968F81`.
Hairlines `rgba(232,228,218,.11)` and `.22` — alpha, so they track the surface.
**Accent is ink** (`--accent: #E8E4DA`); there is no chromatic accent.
Colour appears only where it encodes direction: win `#4FB07A`, loss `#E8695C`,
amber `#D9A03C`, info `#8FA3BE`.
Bricolage Grotesque (ink-trap, variable, `opsz` axis) for display; JetBrains
Mono for every numeric; `tabular-nums` on all data. **All four radii are `0`;
both shadow tokens are `none`.** Two durations (120ms / 180ms), one easing,
transform+opacity only; `prefers-reduced-motion` collapses all.

Contrast is computed in Python against the **worst** surface, which on a dark
ground is the **lightest** one (`--bg-elev #262219`), not the base. Body ≥ 4.5:1
there. Current floor: `--text-dim` at 4.94:1.

Checklist items 2 and 8 (one accent, three role-bound radii) are satisfied by
elimination here — do not mark them failed for being absent.

## SLOP BLACKLIST (any hit = fix before showing the user)

1. Indigo/violet (hue 240–280) sole accent; blue→purple gradients.
2. Gradient text. 3. Glassmorphism/backdrop-blur panels. 4. Colored glow
shadows / luminous artifacts. 5. Eyebrow-badge above oversized headline;
per-section uppercase kickers. 6. Rows of identical icon-tile cards.
7. Card-in-card same radius/shadow; 24px+ radii; colored edge-stripe cards.
8. Decorative 01/02/03 ordinals; big-number stat strips. 9. Copy: verb-pair
aphorisms, buzzwords, captions explaining the design. 10. One spacing value
everywhere; compressed type scale. 11. Bounce/overshoot easing;
scale-on-hover; animating layout properties. 12. Drop shadows on dark UI
(elevation = luminance steps). 13. Sub-AA body contrast. 14. Uncomputed
nested radii (inner = outer − gap). 15. The template arrangement: left
sidebar + KPI-card row + center chart.

## CRAFT CHECKLIST (≥13 of 16 must pass; grade a SCREENSHOT, not intentions)

1. Luminance-ladder elevation + 1px hairlines, zero box-shadows.
2. Exactly one chromatic accent beyond win/loss/live semantics.
3. tabular-nums on all data; proportional in prose; never mixed.
4. Numeric columns right-aligned; text left; headers share column alignment.
5. No zebra striping; hairline horizontal rules only.
6. Size-coupled tracking (negative at display sizes → 0 at body; positive
   only on ≤11px caps); weights 400–560.
7. 4px grid; tighter within groups than between; outer padding ≥ inner gaps.
8. Three role-bound radii only; nested radii computed.
9. One full-contrast element per row; metadata muted; actions on hover.
10. Closed status vocabulary: word-label tinted pills, non-interactive.
11. Stripe filter chips: unset (+) vs active (×) states; Clear only when active.
12. Designed empty/loading states; "no data yet" ≠ "filtered to zero".
13. Neutrals tinted <5% toward accent (one temperature); no pure #000/#FFF.
14. Probability is the lead number per row; odds secondary; color = direction.
15. Visible keyboard affordances (focus rings, keycap hints).
16. Motion on transform/opacity only; reduced-motion respected.

## Dual-audience + education rules (square AND sharp; researched 2026-07-19)

- Lead every probability with natural frequency ("7 in 10") + precise % adjacent;
  translate odds to dollars for the USER'S stake, never the $100 convention.
- Task-critical numbers NEVER live only in tooltips. Tooltips = definitions only,
  ≤150 chars, uniform dotted-underline affordance on EVERY jargon term
  (edge/EV/CLV/vig/Kelly/fair line), hover + keyboard-focus + tap parity.
- Progressive disclosure: max TWO levels; disclosure labels carry information
  scent ("Show the working — fair line · splits · distribution", never "More").
  Simple default is correct pedagogy — don't show everything to "teach".
- Simplify presentation, never data (Robinhood lesson): every plain number
  expands to its full accurate breakdown. Calm, factual confirmations — no
  confetti, no streaks, no urgency mechanics. Stake presets anchor LOW.
- Education is pull, not push: point-of-encounter, once, dismissible,
  re-findable via a persistent "?" route; never explain standard controls.
- Sharp layer requirements: CLV per bet + aggregate (it's the pro scoreboard),
  devigged fair-line anchor visible per row, staleness timestamps on prices,
  persistent filters, computed fractional-Kelly stakes, one-click drill-down.
- Distinctiveness levers (never decoration): ONE stated opinion enforced
  everywhere; ≤2 motion durations as identity; six designed microstates per
  interactive element; layout-matched skeletons with staggered reveal;
  structure-as-decoration (visible hairlines, ledger numerals); aggressive
  contrast + doubled whitespace.

## Mandatory process for any UI change

1. Mock in plain HTML/CSS at `design-mock/index.html`, then serve it:
   `python3 -m http.server 8931 --directory design-mock`
2. Screenshot and audit with the committed helper — do not hand-roll one:
   `node tools/shoot.mjs http://localhost:8931/` (add `--out DIR` to redirect).
   It shoots **1440 / 412 / 320**, emulates `reducedMotion: reduce`, waits on
   `document.fonts.ready`, and reports overflow + axe serious/critical per width.
   **412, not 390** — 412 is the only mobile width that has a Playwright
   baseline (`playwright.config.ts` → Pixel 7), so grading at 390 grades a
   width nothing else in the repo tests.
3. Grade the screenshot line-by-line against BOTH lists above. Fix, re-shoot,
   repeat ≥3 rounds. Show the user only what survives.
4. Never edit mock HTML with sed/regex — rewrite files cleanly.
5. Real product content only — no lorem, no invented marketing copy.
6. **Grade the shipped page, not only the mock.** The mock and `theme.css` are
   separate artifacts and have drifted before; the user sees the app.
7. Full research provenance: docs/superpowers/specs/ + the brain node
   ui-phase2-design-direction.
