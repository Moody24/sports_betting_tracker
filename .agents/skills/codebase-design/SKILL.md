---
name: codebase-design
description: Use only when explicitly requested to review responsibility ownership or an interface before a focused refactor.
license: MIT
---

# Codebase Design for Edge Tracker

Local adaptation of mattpocock/skills at `49dd158d1076134a641b33efb035946536778336`.

Trace the current callers and tests before proposing a seam. Prefer an interface
that hides useful complexity and keeps one responsibility in one owner. Describe
the behavior, invariants, failure modes and configuration a caller must know.
Choose a change that improves locality and testability, then state what existing
behavior and provider or security boundaries must remain.

The source uses a strict design glossary and a parallel-agent design exercise.
Neither is required here. Do not rename existing APIs to satisfy terminology,
split files only to reduce file size, remove a boundary because it has one
implementation, or dispatch alternative-design agents automatically.

The canonical ownership map is [the system contract](../../../docs/architecture/system-contract.md).
