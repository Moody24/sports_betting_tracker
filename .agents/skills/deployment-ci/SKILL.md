---
name: deployment-ci
description: Use for Edge Tracker CI, release configuration, migrations, security gates, Gunicorn or deployment tasks.
---

# CI and release

Read `.github/workflows/ci.yml`, [deployment runbook](../../../docs/deploy.md)
and [the system contract](../../../docs/architecture/system-contract.md) for
the task. CI covers Ruff, Bandit, tracked-file and history secret scans,
dependency audit, `unittest` coverage and PostgreSQL migration parity.
Run focused checks while editing and the required complete gates before
integration. Record actual results; do not repeat a past coverage number as
current state.

The safe hosted baseline is one Gunicorn web worker with the scheduler
disabled and at most one dedicated scheduler process. Multiple web workers
need the shared limiter store required by the app. Review migrations before
deployment. Do not infer live hosting or provider access from retained files.
Check branch state and working changes before a push; do not pull or rebase
automatically. Keep generated caches, reports and real data out of commits.
