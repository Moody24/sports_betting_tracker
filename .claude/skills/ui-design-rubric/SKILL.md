---
name: ui-design-rubric
description: "INVOKE for ANY Edge Tracker UI/design work — new screens, mockups, component styling, or design review. Encodes the user's design taste (three rejected directions), the research-derived slop blacklist + craft checklist, the reference anchors (Linear/Stripe/Mercury/Polymarket/DK), and the mandatory build→screenshot→grade loop."
---

## The design bar (learned the hard way — three rejections)

The user's bar: "would a senior front-end dev at Linear/Stripe ship this?"
Rejected directions, never to be resampled:
1. **Amber/gold system** (June 2026) — "looks stupid," dated.
2. **Aurora kit** — violet-teal gradients, glow filters, glassmorphism,
   gradient text, radial atmosphere, cute chart captions → "AI slop."
3. **Cheap flat-minimal** — a dark table with default-feeling styling →
   "bland." Restraint WITHOUT craft is equally a failure.
Also: "normal-looking" is not enough — the design must earn distinctiveness
through information design, signature data displays, and voice, never
through decoration. Serve BOTH square (novice) and sharp (pro) bettors:
plain-language explanations available everywhere, density preserved.

## Reference anchors (user-named)

- **Linear** — skeleton: spacing, typography, keyboard flow, luminance-ladder
  dark elevation (no shadows), one accent.
- **Stripe Dashboard + Mercury** — data surfaces: table discipline, filter
  chips, closed badge vocabulary, tabular numerals on all money/data.
- **Polymarket** — probability-first rows; markets as browsable content.
- **DraftKings/FanDuel** — betting furniture: odds chips, slips, prop cards.

## Working tokens (approved direction, "Board" mock 2026-07-18)

Graphite ladder `#0B0C0E → #101114 → #141619 → #191B1F`, hairlines
`#1E2126/#282C33`, text `#EDEEF0/#9CA1A9/#63686F`, single accent cobalt
`#5872E8`, semantics `#3FB27F` up / `#D4544E` down. Inter Variable only
(`font-feature-settings:'cv01','ss03','zero'`; emphasis = wght 510, never
bold), `tabular-nums` on every data number. Radii: 6px controls / 10px
containers / 999px pills — nothing else. Motion: 120–200ms ease-out on
transform/opacity/background only; `prefers-reduced-motion` collapses all.

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

## Mandatory process for any UI change

1. Mock in plain HTML/CSS at `design-mock/index.html` (worktree), serve via
   `.claude/launch.json` (`python3 -m http.server 8931 --directory design-mock`),
   open with the preview browser.
2. Screenshot at desktop AND mobile widths. Never present unrendered code.
3. Grade the screenshot line-by-line against BOTH lists above. Fix, re-shoot,
   repeat ≥3 rounds. Show the user only what survives.
4. Never edit mock HTML with sed/regex — rewrite files cleanly.
5. Real product content only — no lorem, no invented marketing copy.
6. Full research provenance: docs/superpowers/specs/ + the brain node
   ui-phase2-design-direction.
