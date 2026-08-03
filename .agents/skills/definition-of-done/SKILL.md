---
name: definition-of-done
description: "INVOKE for any frontend, UI, or template change in Edge Tracker — covers responsive breakpoint requirements, live-progress row fields, control regression checks, and the full definition of done checklist."
---

## Definition of Done — UI / Frontend Changes

### Responsive Layout
- No horizontal overflow at `320px` viewport width on the bets list
- No overlap between status / P&L / actions at breakpoints: `1200`, `992`, `768`, `576`, `375`

### Live-Progress Rows
Every live-progress row must show all of:
- Current stat
- Line
- Period
- Clock
- Game state
- Projection
- Trend

### Over/Under Trend Semantics
Validate with at least one concrete **over** example and one concrete **under** example before shipping.

### Control Regression
Verify these existing controls remain unchanged after every UI change:
- Filters
- Search
- Export
- Add (new bet)
- Check now
- Manual grading
- Parlay toggle
- Delete

### Testing & Documentation
- Update tests for endpoint payload and key render paths
- If visual changes are substantial: include before/after screenshots for desktop and mobile widths
