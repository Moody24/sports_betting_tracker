---
name: sheet-contract-auditor
description: Historical Sheet migration role retained for Claude-host compatibility. Do not invoke for new work; use AGENTS.md and current UI contracts.
model: inherit
color: cyan
tools: ["Read", "Grep", "Glob", "Bash"]
---

# Historical Sheet migration role

This entrypoint remains for Claude-host compatibility. The completed migration
sequence is historical; do not dispatch the old agent chain for new work.
Use `AGENTS.md`, `docs/architecture/system-contract.md`,
`docs/ui_v1_baseline.md`, and the relevant project skill for the current task.
Assign one writer per shared UI file, run checks for the affected surfaces, and
report exact results. Historical role instructions remain in Git history.
