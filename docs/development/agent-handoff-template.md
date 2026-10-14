# Agent task handoff template

Copy this structure into a unique mode-0700 task run directory outside the
repository. The coordinator owns `brief.md`; each writable agent owns one file
under `handoffs/` named by its unique task ID. Two agents with the same role
must have different files. Use task-specific IDs, never credentials or raw transcripts.

## `brief.md` — coordinator writes

- Task ID and intended outcome:
- Base commit and branch/worktree:
- Acceptance checks and prohibited side effects:
- File ownership: role → exact paths; overlaps requiring negotiation:
- Shared interfaces or contracts that may change:
- Integrated revision: `HEAD`, SHA-256 of `git diff HEAD --binary --no-ext-diff`, and sorted untracked path/content hashes:
- Status and next owner:
- Decisions and message IDs/timestamps:

## `handoffs/<agent-id>.md` — named agent writes when permitted

- Role, assigned paths, brief revision read, and message ID/timestamp:
- Work done or findings, with source paths and lines:
- Contract/interface changes requiring another owner:
- Exact checks, exit status, failures, and skips:
- Blockers, risks, and next requested action:
- Response to prior feedback and revision checked:

If the agent is read-only, it sends findings by message and the coordinator
records them in its own handoff; it does not try to write a file. Otherwise,
send a message after writing the handoff. The recipient reads the named file,
acknowledges the revision, and replies with a scoped action or finding. A shared
file alone does not deliver or wake a message. The coordinator records the
response and ensures review and test refer to the same integrated revision.
