# Bounded coding workflow

Use native Codex agents only when a change benefits from independent work. This is
project guidance, not an automatic runner, lifecycle hook, or application dependency.
For a small documentation or low-risk edit, one agent makes the edit, checks the diff,
and reports the result. Do not create a team merely to follow a template.

## Coordinator, owners, and shared state

The parent session is the coordinator. At startup it reads
[project memory](project-memory.md), checks the actual branch and diff, and
loads only relevant decision and evidence records. It defines the intended behavior, acceptance
checks, base revision, file owners, and integration decision. It may implement directly
or delegate to `edge_implementer`, `edge_frontend`, or `edge_backend` for a
specific file domain. A scoped implementer may own disjoint test files
while another owns application files. A domain agent may write a disjoint assigned area,
but never a file another active agent owns. The coordinator resolves interface changes
and overlapping edits before work continues. If genuinely concurrent writers need
overlapping files, give them isolated worktrees, review their patches, and integrate
sequentially. Worktrees separate edits; they do not isolate a database, processes, or
credentials. `edge_reviewer` reviews and `edge_tester` verifies the integrated diff.
Use `edge_ui_ux` for scoped usability review and `edge_security` for scoped
trust-boundary review when the changed surface calls for them. These are
read-only reviewers, not mandatory gates. Neither reviewer nor tester edits
source or starts agents. Use at most two child agents concurrently,
only when useful; there is no standing team.

For a delegated task, the coordinator creates a unique private directory outside
the repository with `mktemp -d "${TMPDIR:-/tmp}/edge-agent.XXXXXX"` (mode 0700)
and a `brief.md` based on
[the handoff template](agent-handoff-template.md). The coordinator alone updates the
brief and assignments. Each writable agent owns a unique `handoffs/<agent-id>.md`
named by its task ID, even when two agents use the same role; it reads the brief
plus relevant prior handoffs. A read-only reviewer or tester sends findings through
messaging, and the coordinator records them in its own handoff. Record the base revision, changed paths,
contract or interface changes, checks, blockers, and message IDs or timestamps.
Keep credentials, real bet records, and raw transcripts out of these files. The
coordinator removes only its verified run directory after handoff; it never sweeps
unrelated temporary files. These files are durable shared context, not a task queue.

Use native agent messaging or follow-up controls to announce a handoff and request a
response. If direct peer delivery is unavailable in the active client, route the
message through the coordinator, naming its intended recipient. A file edit alone
does not wake another agent. The coordinator forwards reviewer/tester findings to
the file owner, obtains the owner's response, and returns changed behavior to the
checker for a targeted recheck. Report a failed or unverified delivery explicitly.

Project role files live in `.codex/agents/`. They inherit the parent session's
model and reasoning effort because they have no model pins. They also inherit the
parent's sandbox and approval settings. Their no-edit instructions are behavioral;
for enforced read-only review, launch that work in a read-only session and use
message-only findings. Never grant additional permissions solely to make a role work.

## One review cycle

1. Inspect the current owner, callers, contract, and relevant tests. Choose a small
   behavior slice, assign file owners, and send each delegate its scoped brief.
2. Owners read relevant handoffs and implement their assigned change. An owner
   requests a boundary decision before editing another owner's files. For behavior
   changes, run a focused `unittest` regression check. Preserve existing security
   and provider boundaries.
3. Freeze edits while independent checks run. Record `HEAD`, intended behavior,
   the SHA-256 of `git diff HEAD --binary --no-ext-diff`, and a sorted list of
   each untracked path with its content SHA-256. Give reviewer and tester those
   same identifiers and changed paths. Recompute them when both finish; if any
   content changes, restart the affected review or check against the new diff.
4. The coordinator relays substantiated findings to the responsible owner. The
   owner records its response and fix in its handoff; the coordinator passes that
   response to the checker. Rerun focused checks and ask for a targeted recheck
   when the fix materially changes reviewed behavior. Use no more
   than two targeted fix cycles without a new diagnosis; then reassess and report
   the blocker instead of repeating guesses.
5. The parent runs the required integration gates at the appropriate point, reviews
   the final diff, and reports exact commands, failures, skips, residual risks, and
   rollback paths. A focused pass is not a full-suite pass. Then update
   [change evidence](change-evidence.md), decisions, and verified lessons only
   where finished work established facts. Identify the commit or local diff so
   later tasks can recheck the record.

Permission barriers, conflicting changes, unclear requirements, or a real-data
operation require a separate decision. Do not use this workflow to start live jobs,
alter databases, retrain models, publish, or weaken checks.

## Current client limits

The project has no automatic loop or model-specific role assignment. Local CLI
0.150.1 reported the user default `gpt-6-sol` with `high` reasoning, but a fresh
ChatGPT-account CLI turn rejected that model. A temporary config override with
`gpt-5.6-sol` worked. There is no project model pin; the owner will select a
model interactively. Role files inherit the parent session's choice and
reasoning effort. The user default for other projects is unchanged.
Read-only CLI smoke turns also invoked the `edge_reviewer` and `edge_tester`
roles and received the requested heading. Their no-edit instructions were
tested under a parent read-only sandbox; role prose alone does not enforce it.
Recheck model availability before changing project model settings. CLI 0.150.1
reports the global Ponytail plugin disabled in this checkout, but an earlier
prompt inventory still listed its global skill. Plugin status does not prove
that every skill catalog has filtered it or that hooks cannot run.

LangChain and LangGraph are not dependencies of this coding workflow. Native
Codex sessions cover the current single-writer review and test steps without a
separate API key, graph state service, or application runtime integration.
On 2026-10-14, agents in the current task runtime exchanged a direct peer
message and read each other's private handoffs; a checker also rechecked two
coordinator fixes. This does not establish direct peer messaging in the separate
installed CLI, which still needs its own harmless turn after the owner selects
an available model. Use coordinator relay until that behavior is verified there.
