# Harness VPS Migration — Design (Increment 1)

**Date:** 2026-07-16
**Status:** Draft, awaiting review
**Supersedes:** the "step 6 — custom API orchestration ⏸ deferred" line in `[[dual-agent-harness]]`

> This spec lives in `sports_betting_tracker` only because the harness repo does
> not exist yet. Task 1 creates that repo; this file moves there as its first
> commit and is deleted from here.

---

## 1. Problem

The dual-agent harness (Claude Code + Codex, shared brain vault, file-bus, SDD
loop) works, but is pinned to one machine with two consequences:

1. **The user is the message bus.** Claude writes a spec, the user reads it,
   opens Codex, pastes a pointer, comes back, reads the diff, returns to Claude
   for review. The file-bus carries the *artifacts*; the human carries the
   *baton*. This is the single largest source of friction and the reason work
   cannot continue unattended.

2. **The host is a 2-core Intel i3-1000NG4 MacBook Air with 8GB RAM.** Measured
   consequences, not estimates:
   - Brain embedding runs at **1.29 chunks/s** (`tools/brain_embed.py`).
   - The ONNX backend was built to fix this and delivered **1.0x** — see
     `[[onnx-embedding-dead-end]]`. The bottleneck is the hardware.
   - XGBoost retrains and the 1.64M-row ScenarioSplit refresh compete with the
     OS for 8GB.

Work stops when the machine is closed, and stops when both agents hit their
usage limits.

## 2. Decisions

| # | Decision | Rationale |
|---|---|---|
| D1 | **Hetzner CPX41 (x86): 8 vCPU / 16GB / 240GB, ~€30/mo** | RAM is binding (8GB today includes macOS). Cores buy embedding throughput directly — commit `1b7933a` made embedding use all logical cores, so 2→8 cores should take 1.29 → ~5 chunks/s. x86 preserves every benchmark already recorded ("on x86" per the ONNX note). |
| D2 | **The VPS is the harness's single home and the vault's sole writer.** Laptop becomes a thin client. | `tools/brain_lock.py` uses `fcntl.flock`, which is **host-local**. Two checkouts = two independent locks that cannot see each other, failing *silently* and corrupting the ledger. One writer keeps the existing lock correct as designed rather than requiring a distributed lock. |
| D3 | **The harness gets its own repo.** Projects become config entries. | SDD state currently lives at `sports_betting_tracker/.superpowers/sdd/` — the harness is a guest inside one project. A second idea would fork the machinery. Config-per-project means a new idea is a config file, not a new harness. |
| D4 | **Subscriptions stay primary. API is overflow only (~$20 float). Open models via OpenRouter are the bottom tier.** | Usage is heavy (a Max weekly limit was hit 2026-07-15) and heavy usage is exactly where flat billing wins. Metering the current pattern at Opus rates ($5/$25 per MTok) would cost multiples of the subscription. API's real job is *continuity when both subscriptions are exhausted* — insurance, not budget. |
| D5 | **No GPU, no self-hosted open-weight models.** | The stated goal was "work never stops." A rented GPU costs ~$900/mo and bills while idle; API overflow costs $0 on days it isn't needed and gives the same guarantee. Open models remain available via hosted APIs (no GPU) for mechanical work. |
| D6 | **No vector DB.** Keep BM25-first hybrid retrieval. | Corpus is 54 wiki nodes + a few hundred session files — brute-force cosine is instant. A vector DB solves ANN-at-millions-scale, a problem not present. Independently corroborated: Hermes Agent uses SQLite **FTS5** (`messages_fts`, `messages_fts_trigram`) for episodic recall — lexical, no vectors. |
| D7 | **Do not adopt Hermes Agent. Steal from it.** | `acp_adapter/server.py`: *"exposes Hermes Agent via the Agent Client Protocol"* — Hermes is the ACP **server**; editors drive it. Grepping the full 232MB repo for `claude-code`/`codex`/`opencode`/`gemini-cli` returns **zero hits**. Hermes is a *peer* to Claude Code, not a conductor. Adopting it discards the subscriptions, the skills ecosystem, and cross-vendor review independence. |
| D8 | **Control = Telegram bot (push). Visuals = local page (pull).** | A dashboard is passive: an agent blocked at 2am waits until someone opens a browser. A bot pushes to the phone and takes a reply. The bot token *is* the auth — no public port, no auth system to get wrong. Proven pattern: Hermes ships `hermes cron create ... --deliver telegram`. A page still serves the things chat renders badly (run timeline, cost, graph). |
| D9 | **Usage logging lands in Increment 1**, not Increment 3. | Hermes's entire "learning loop" reduces to markdown + frontmatter links + a `skills/.usage.json` counting `use_count` / `last_used_at`. `brain_search.py` already computes which nodes it returns and discards it. ~20 lines. Every day it isn't running is history that cannot be recovered. |

### Non-goals (explicitly out of scope)

- API keys as the primary execution path (D4)
- GPU rental or local open-weight inference (D5)
- A vector database (D6)
- Migrating onto Hermes Agent (D7)
- Harness self-grading — needs recorded history first (see §6)
- Internet-exposed control surface — **never**; see §5

## 3. Architecture

```
Hetzner CPX41 (single host, sole writer)
├── harness/                    NEW repo — orchestrator, bot, page, run store
│   ├── orchestrator/           routing + headless dispatch
│   ├── runs.db                 SQLite: runs + events  ← the spine
│   ├── bot/                    Telegram: approve · kill · start · status
│   ├── web/                    Flask: timeline, cost, brain growth
│   └── projects/*.yaml         one config per project
├── claude_brain/Claude-brain/  memory · sole writer · flock valid again
└── projects/sports_betting_tracker/

Laptop ──SSH──> VPS          (thin client)
Phone  ──Telegram──> bot     (control + interrupts)
GitHub <──push── both repos  (durable backup)
```

**Auth:** Claude Code and Codex CLI each log in with their own subscription on
the VPS. No API keys in the default path.

### The run store is the spine

```sql
runs   (id, project, task, agent, status, started_at, ended_at, tokens, cost_usd)
events (id, run_id, ts, kind, payload)
```

Everything else is a view over this. The Telegram bot reads it to answer
"what's running". The page reads it to draw the timeline. Harness self-grading
(Increment 3) is a *query* over it — which is precisely why it cannot come
first: it needs history, and history starts when the orchestrator starts
writing.

### The orchestrator

Replaces the user as message bus. Per task:

1. Read the spec file (existing file-bus format).
2. Route via the checklist already written in `[[dual-agent-harness]]` — budget
   first, then capability.
3. Dispatch **headless** to the chosen agent's CLI, on its subscription.
4. Commit the result.
5. Hand the diff to the **other** agent for independent review (implementer ≠
   reviewer, preserved).
6. Write `runs`/`events` throughout.
7. Escalate to Telegram at decision gates.

This encodes existing conventions rather than inventing new ones.

## 4. Increment 1 — scope

1. Create the `harness` repo; move this spec and the SDD machinery out of
   `sports_betting_tracker`.
2. Provision the CPX41; base hardening (non-root user, SSH keys only, firewall,
   no password auth).
3. Install Node 20 + Python; clone both repos; log both CLIs into their
   subscriptions.
4. Verify the vault: rebuild `index/` and `models/` (both gitignored,
   regenerated by `brain_index.py` / `export_onnx.py`); **re-measure embedding
   throughput** and record the delta against 1.29 chunks/s.
5. Run store: schema + writer.
6. Orchestrator: single-task dispatch, headless, one agent, emitting events.
   (Two-agent handoff follows once single-task is proven.)
7. Usage logging in `brain_search.py` → per-node retrieval counts (D9).
8. Read-only page: what ran, what it cost, what's running now.

**Done when:** a task can be filed on the VPS, dispatched headless to one agent
on its subscription, and observed to completion on the page — with the laptop
closed.

## 5. Security

- **The control surface is never exposed to the internet.** A page that can
  launch an agent can run arbitrary code on the box. Telegram (bot token as
  auth) or SSH tunnel / Tailscale. No public port, no password form.
- **`raw/web/` is never pushed.** It is gitignored on purpose and holds private
  personal material. Verified absent from the remote 2026-07-16
  (`gh api .../contents/raw/web` → 404). That line does not get removed, and the
  exclusion is re-verified before any change to vault syncing.
- Secrets live in the environment, never in the vault or the run store.
- API overflow keys (when added) are scoped and spend-capped at the provider.

## 6. Later increments (sketched, not specified)

**Increment 2 — control plane.** Telegram bot: approve a gate, kill a run,
start a run, status. Two-agent handoff automated end to end.

**Increment 3 — the three learning views**, in the order chosen:
1. *Brain growth* — nodes added, sessions ingested, what it learned. Nearly
   free; the vault is an **Obsidian** vault, so the wikilink graph already
   renders natively. Usage counts (D9) make it meaningful by showing which
   knowledge is actually load-bearing versus dead weight.
2. *ML backtesting surface* — Plan C's distributional core, walk-forward
   validation. Data already exists; this is a view.
3. *Harness self-grading* — did the reviewer catch real bugs? did routing pick
   right? did a plan need rework? Hardest, and last: needs `runs`/`events`
   history plus an agreed definition of "good."

## 7. Open questions

- **Where does the local page bind?** Loopback + SSH tunnel is simplest and
  safest. Tailscale is nicer from the phone. Decide at Increment 2.
- **Backup beyond GitHub?** `runs.db` and `instance/app.db` (405MB) are not in
  git. Hetzner snapshots are the cheap answer.
- **Does `psycopg2-binary` stay?** The tracker runs SQLite; the Postgres driver
  is a Railway/Neon leftover. Unrelated cleanup — noted, not scoped.
- **What is "good" for self-grading?** Deliberately unanswered until there is
  history to look at.
