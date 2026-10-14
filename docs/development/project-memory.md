# Project memory

Read this short index at task start, then the current code and only the relevant
linked records. This is a navigation aid, not runtime truth. Never store secrets,
real bet records, raw agent transcripts, or unverified claims here.

## Current state

- The canonical architecture is [system-contract.md](../architecture/system-contract.md);
  [ARCHITECTURE.md](../../ARCHITECTURE.md) is the onboarding map. Inspect code
  and tests before relying on either for changing behavior.
- The current work is on `codex/community-skill-stack`. It adds selected
  repo-local skills, scoped role guidance, and a bounded truthful no-data
  Stat Analysis fix. Check `git status`, `git log`, and [change evidence](change-evidence.md)
  before continuing; the branch and its diff may change.
- [Codex stack](codex-stack.md) records skill pins and known activation limits.
  [Coding workflow](coding-workflow.md) defines scoped roles and handoffs.
- No-data Stat Analysis rows use `unavailable` when no player games and no
  model score exist; scored no-edge rows remain `avoid`. The route's
  `_stat_indicator` and `INDICATOR_TIERS` own the badge semantics, and the
  template masks model-only numbers in its table and slide-out. Read those
  implementations and tests before further UI or scoring changes.

## Working backlog

These are candidates, not approval to change production behavior. Revalidate
against the current code and pick one behavior slice per task.

1. Determine scoring and cache process ownership and invalidation semantics.
2. Examine scheduler/coordinator coupling only where a demonstrated ownership
   issue makes a small change worthwhile.
3. Verify job-health alerts against each job's cadence, skipped work, and
   never-run state.
4. Investigate large NBA services through actual callers and repeated change
   costs, not file size alone.
5. Verify integration safeguards and history coverage before proposing settings
   changes; repository setting changes need separate approval.

## Durable records

- [Decisions](decisions.md): accepted project choices and their reasons.
- [Change evidence](change-evidence.md): completed changes, exact checks, and
  limitations, keyed by commit or dated local diff.
- [Verified lessons](verified-lessons.md): conclusions backed by a check or
  incident, with a link to evidence and a recheck trigger.

At integration, the coordinator updates only facts established by the finished
change. Replace stale entries rather than appending duplicate status narratives.
