# Pinned Taste Skill admission review

Reviewed 2026-10-07 under `agent-skillguard`. The user explicitly authorized project-local cloning and configuration after the initial read-only review.

The selected upstream skill directory contains one regular Markdown file. All 1,206 lines were manually reviewed in the initial review. The cloned copy is byte-identical to that reviewed file, confirmed with `cmp`, SHA-256, and the upstream Git blob. No repository installer, shell script, JavaScript example, package command, or third-party dependency was executed.

## Scanner evidence

Command attempted before installation:

```text
skillguard scan /Users/mohamoudmohamed/Documents/Codex/2026-10-07/task/skill-review/upstream/skills/taste-skill --format markdown
```

Initial result: exit `127`, `zsh:1: command not found: skillguard`. The pre-install fallback was the completed manual review; this was not reported as an automated pass.

After the skill catalog refreshed, the installed agent-skillguard 0.1.5 package exposed its bundled `scripts/skillguard.py`. The same pinned artifact was then scanned read-only with that script. The scan completed with exit `0`: **0 active findings, 0 suppressed findings, 1 file scanned**. See [references/scan-upstream.md](references/scan-upstream.md). No rules were suppressed. The final project directory is also scanned, with its separate report in `docs/frontend/taste-skill-scan.md`. A clean deterministic scan is not a safety certification; the manual compatibility findings below still apply.

## Manual findings in references/upstream-SKILL.md

| Lines | Finding | Project handling |
| --- | --- | --- |
| 8, 898-906 | Scope excludes dashboards, data tables, and product forms. | Route these surfaces to the project `interface-design` and definition of done. |
| 99-102, 127-143, 991-1025 | Framework defaults and unpinned package-install commands can expand scope. | Keep Flask/Jinja/vanilla CSS and current dependencies. Do not run reference install commands. |
| 267-278, 626 | Image generation, external image hosts, and logo CDNs are suggested. | Prefer current assets or a real product screenshot; do not introduce external services automatically. |
| 327-330, 616-618 | Conflicting advice about real versus realistic invented numbers. | Use verified claims and visible example labels. |
| 1029-1030 | Shopify-only API-key placeholder and external script example. | Contextual example, not observed credential theft; irrelevant to Edge Tracker and excluded. |
| 914-979 | Broad marketing/framework checklist can overrule product needs if applied blindly. | Use only checks relevant to the affected surface; preserve data density and controls. |

## User-authorized project overrides

The user's later design feedback supersedes the original typography and sidebar direction. The wrapper now specifies compact neutral sans-serif type, tabular numerals, game/market-first top navigation, and no permanent sidebar. PropFinder is the primary hierarchy/density reference. Product tables and live context remain intact under `interface-design` and the project definition of done.

The user also requested original functional SVGs, overriding upstream icon-library defaults. The wrapper requires consistent strokes, meaningful symbols, text labels/accessibility, and no copied proprietary assets. These changes affect project guidance only; the pinned upstream Markdown and MIT license remain byte-identical. The package is rescanned after the wrapper changes, with the result recorded in `docs/frontend/taste-skill-scan.md`.

The selected file contains no identified instruction to read secrets, upload private files, exfiltrate data, bypass safeguards, grant broad permissions, or perform destructive commands. This conclusion is limited to the pinned selected Markdown file and the project wrapper. The rest of the upstream repository and referenced third-party packages are not admitted by this review.

The clone uses sparse checkout for the selected skill and MIT license with Git hooks disabled. It is outside the application repository. The application contains only this compatibility entrypoint, review/provenance files, upstream license, and the unchanged upstream Markdown reference. No global skill was installed.
