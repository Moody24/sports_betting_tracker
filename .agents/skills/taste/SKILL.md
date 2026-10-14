---
name: taste
description: Use only when explicitly requested for Edge Tracker public landing or informational pages; product screens use interface-design.
---

# Taste Skill for Edge Tracker

This is the project compatibility entrypoint for Leonxlnx/taste-skill. The exact upstream skill is preserved in [references/upstream-SKILL.md](references/upstream-SKILL.md), pinned to commit `b482f7a970abb98c4108d4a9f761e458c64cefc8`. Read this entrypoint before applying the upstream reference. See [SOURCE.json](SOURCE.json), [REVIEW.md](REVIEW.md), and [LICENSE.upstream](LICENSE.upstream) for provenance and review limits.

## Choose the relevant surface

- **Public landing, about, or marketing page:** read the upstream reference and apply its contextual typography, hierarchy, composition, spacing, and accessibility guidance through the current Jinja templates and CSS.
- **Dashboard, bet ledger, analysis screen, data table, or product form:** use [interface-design](../interface-design/SKILL.md) and the repository's [definition-of-done](../definition-of-done/SKILL.md). Upstream Taste explicitly excludes these surfaces (lines 8 and 898-906). Preserve information density, required fields, controls, and semantic outcome colors. Shared consistency and accessibility principles remain useful, but marketing rules about removing tables, imagery, cards, or long lists do not apply.
- **Shared navigation, styles, or components:** identify every affected surface and use the stricter product regression requirements. Avoid letting landing-page presentation rules reduce tracker functionality.

## Project compatibility

1. Keep Flask, Jinja, vanilla CSS, and vanilla JavaScript. Reuse `app/templates/base.html`, existing template macros, `app/static/css/theme.css`, and the current frontend modules. Upstream React/Next.js, Tailwind, Motion, GSAP, and icon-package defaults do not authorize a stack migration or new dependency.
2. Preserve route endpoints, `url_for` targets, form field names, CSRF handling, element IDs/data attributes used by JavaScript, analytics hooks, and existing legal/methodology copy unless the user explicitly requests those changes. Follow the repository architecture contract for any behavioral work.
3. Audit the existing interface before changes. Retain its brand and content unless a visual overhaul is explicitly requested. Make typography, spacing, and hierarchy changes first; change structure when it improves the requested flow.
4. Follow the user's updated PropFinder-inspired product direction: compact Arial/Helvetica sans-serif typography, tabular numerals, restrained borders, and dense game/market research panels. The previous Bricolage Grotesque/JetBrains Mono treatment is superseded for product UI; branding and the public hero may retain Bricolage. Reuse semantic CSS tokens and preserve meaningful win, loss, pending, live, warning, and information colors. Use horizontal top navigation with NBA Today, Player Props, Stats, My Bets, and Results; do not restore a permanent sidebar. Preserve the underlying route endpoints.
5. Keep information readable: concise headings, clear action hierarchy, visible input labels, inline errors, honest empty/loading/error states, readable contrast, keyboard focus, and useful touch targets. Product density comes from alignment and spacing, not tiny text or missing data. Preserve necessary tables, filters, live context, and supporting explanations rather than applying the upstream marketing-page limits to them.
6. Keep motion low and purposeful. Use existing CSS hover/focus/active feedback and honor `prefers-reduced-motion`. Do not add scroll hijacking, animated marquees, WebGL, or new animation libraries just to satisfy an upstream dial.
7. Use current local assets or an actual product capture when imagery serves the page. Upstream image-generation, stock-host, logo-CDN, and package-install suggestions are optional references, not commands to run. Do not fabricate endorsements, users, returns, win rates, or other proof; label examples visibly.
8. Treat installation commands and external scripts in the upstream appendices as reference only. This setup installs no application dependency, does not activate repository scripts, and grants no new permissions. Any future dependency should be justified by the user task and reviewed separately.
9. Use the user-requested custom functional SVG system through the existing Jinja icon macro. Keep a consistent 24px viewBox, stroke weight, joins, and `currentColor`; retain navigation text and accessible names on icon-only controls. Use game, player-market, statistics, bet-slip, and results concepts. Do not use a generic game-controller glyph or copy proprietary artwork. This explicit preference overrides upstream icon-library defaults. PropFinder provides hierarchy and density inspiration, not branding or asset ownership.

## Review before delivery

- Load the project `definition-of-done` for actual UI/template changes. Check its required breakpoint, live-progress, and control-regression requirements.
- Check affected screens at desktop and mobile sizes, including the 320px bets-list overflow requirement. Keep tables navigable and retain the complete live-progress fields.
- Inspect keyboard navigation, focus visibility, labels, contrast, reduced motion, and empty/loading/error states. Check actual actions, not only appearance.
- For substantial visual changes, retain before/after screenshots and run the existing focused render/browser checks. Report what ran and any limits honestly.

The setup itself changes only skill guidance and documentation. It is not evidence that all UI checks have passed or that every upstream aesthetic rule is implemented.
