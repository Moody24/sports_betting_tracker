---
name: definition-of-done
description: Use for actual Edge Tracker frontend, template or shared UI changes to check affected behavior and responsive layouts.
---

# UI definition of done

Preserve [the UI release baseline](../../../docs/ui_v1_baseline.md) and verify
the affected screens. For shared navigation, styles, macros or JavaScript,
broaden verification to every affected surface and use one writer per file.

- No document overflow at 320px on the bets list. Check relevant widths at
  1200, 992, 768, 576 and 375px; status, P&L and actions must not overlap.
- Live progress retains current stat, line, period, clock, game state,
  projection and trend.
- Check one concrete over and one under example for trend semantics.
- Verify affected filters, search, export, add bet, check now, manual grading,
  parlay toggle and delete controls. Check untouched controls when shared code
  changes.
- Preserve focus, labels, contrast, keyboard use, reduced motion, meaningful
  status colors and honest loading, empty and error states.
- Keep task-critical numbers visible without a tooltip. When explaining
  probabilities, pair plain-language frequency with the precise percentage;
  translate odds using the user's stake. Never substitute zero for missing
  closing-line data or imply proven profitability without evidence.
- Run relevant endpoint/render tests. For substantial visual changes, compare
  desktop and mobile screenshots of the actual page.
