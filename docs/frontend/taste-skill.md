# Taste Skill setup

Taste Skill is installed locally for this project at `.agents/skills/taste/SKILL.md`. Invoke `taste` for frontend work or read that entrypoint directly. It chooses relevant upstream guidance and preserves the application's Flask/Jinja, vanilla CSS, and vanilla JavaScript architecture.

## Source and installation

- Repository: <https://github.com/Leonxlnx/taste-skill>
- Exact commit: `b482f7a970abb98c4108d4a9f761e458c64cefc8`
- Clone: `/Users/mohamoudmohamed/Documents/Codex/2026-10-07/task/skill-review/upstream`
- Original skill: `skills/taste-skill/SKILL.md` in the clone.
- Project entrypoint: `.agents/skills/taste/SKILL.md`.
- Unchanged original: `.agents/skills/taste/references/upstream-SKILL.md`.
- License: `.agents/skills/taste/LICENSE.upstream`.
- Hashes and review: `.agents/skills/taste/SOURCE.json` and `.agents/skills/taste/REVIEW.md`.

The clone is pinned with a detached HEAD and sparse checkout of the selected skill and license. Nothing is installed globally. No upstream installer or application dependency was run.

## How the configuration maps to this frontend

| Surface | Guidance |
| --- | --- |
| Landing and public information pages | Contextual Taste typography, spacing, hierarchy, concise actions, and truthful content. Implement through existing templates/CSS. |
| Dashboard, tables, bet records, analysis, and forms | `frontend-design` plus `.agents/skills/definition-of-done/SKILL.md`; these surfaces are excluded by upstream Taste. |
| Shared shell and styles | Game/market-first top navigation, compact neutral sans type, tabular numerals, semantic tokens, functional SVGs, visible focus, and responsive controls. No permanent sidebar. |

The user's updated direction replaces the earlier Bricolage/JetBrains product presentation with `Arial, Helvetica, ui-sans-serif, sans-serif` and tabular numbers. Bricolage is retained for branding and the public hero. PropFinder is the primary reference for research density, restrained panel borders, and market hierarchy. Its player rail and proprietary assets are not copied. Product pages use flat charcoal surfaces, 6-8px panel radii, 22-24px headings, and 13-14px data text.

`base.html` now presents NBA Today, Player Props, Stats, My Bets, and Results across the top, with Add Bet/account utilities. The desktop navbar is 68px high and changes to two rows below 1200px; there is no permanent sidebar. The Jinja icon macro uses original functional SVGs with a 24px viewBox, 1.75px strokes, rounded joins, and `currentColor`; main navigation retains text. The skip link and labeled navigation remain.

The wrapper makes these user-approved choices explicit for future work, including the exception to upstream icon-library defaults. It keeps required tables and live-progress information, uses spacing/alignment to improve density, and preserves readable controls, focus, reduced motion, and semantic outcome colors. This documentation update does not claim to implement or browser-test the UI itself.

The wrapper explicitly preserves meaningful win/loss/live colors, route and form contracts, and actual tracker data. It rejects an automatic React/Tailwind migration, mandatory decorative imagery, invented proof, or new motion packages. It does not enforce the upstream landing-page bans on product tables or necessary long lists.

## Review evidence and future updates

`agent-skillguard` was read and its scan command attempted before installation. The `skillguard` executable was unavailable (actual exit 127), so the selected file received a full manual review. After the skill catalog refreshed, its bundled Python scanner became available. Scanning the pinned upstream skill completed with exit 0, 0 active findings, and 0 suppressed findings. The final project directory also has a recorded scan in [taste-skill-scan.md](taste-skill-scan.md). The cloned, previously reviewed, and installed upstream copies have matching SHA-256 and Git blob hashes. These checks do not certify the artifact as safe or remove the documented scope concerns.

For an update, select a new exact upstream commit, review the selected file again, compare changed directives against the compatibility entrypoint, preserve the MIT notice, and refresh `SOURCE.json`. Do not update the pin silently or run upstream installation scripts as part of reading this skill.
