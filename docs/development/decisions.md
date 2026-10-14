# Decisions

| Date | Choice | Reason / source | Revisit when |
| --- | --- | --- | --- |
| 2026-10-13 | Keep PostgreSQL, Flask/Jinja, vanilla CSS/JS, and `unittest` during Codex setup. | Owner's community-stack handoff and [system contract](../architecture/system-contract.md). | A separate application architecture request is approved. |
| 2026-10-13 | Use selected repository-local skills with explicit invocation; exclude Superpowers and Supabase. | Owner's community-stack handoff; [stack provenance](codex-stack.md). | Owner changes this policy. |
| 2026-10-14 | Use one coordinator and scoped native roles only when useful, with at most two children at a time. | Owner's expanded project-work request; [workflow](coding-workflow.md). | Coordination cost or a concrete task warrants revision. |
| 2026-10-14 | Use Apple HIG only as an optional, secondary review reference for web UI. | Owner's request for an original HIG skill; current product baseline remains authoritative. | Product design direction changes. |
