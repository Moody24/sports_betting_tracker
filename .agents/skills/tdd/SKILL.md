---
name: tdd
description: Use only when explicitly requested to implement an Edge Tracker behavior change test first with existing unittest fixtures.
license: MIT
---

# TDD for Edge Tracker

Local adaptation of mattpocock/skills at
`49dd158d1076134a641b33efb035946536778336`.

Agree the behavior and observable interface once in the task brief. For each
small behavior slice, write a meaningful failing `unittest`, make the smallest
complete fix, then rerun it. Use existing fixtures and validate the final
affected behavior. Expected values must come from a specification or known
example, not a copy of the implementation.

Prefer tests of user-visible or caller-visible behavior. Retain useful lower
level tests and security checks; do not delete them to satisfy an absolute
upstream interface rule. Do not request fresh approval for every test cycle,
invoke the upstream code-review skill automatically, or add a test framework.
