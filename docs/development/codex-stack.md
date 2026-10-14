# Codex project skill stack

Prepared against repository commit `601e3a058b3bd7d554561b7d94fbaaf8841836cb`.
This is project guidance, not an application dependency or a second agent
framework. Installations below are local adaptations, not upstream releases.
Optional skills are explicit-only through each `agents/openai.yaml`. The
application's invariants remain in `AGENTS.md`, the architecture contract,
code and executable tests.
If the active client's picker does not offer a project skill, request an
explicit read of its exact `SKILL.md` link in `AGENTS.md`. This file-based route
works in the current task and does not depend on automatic skill discovery.
It does not count as a successful picker invocation.

| Skill | Pinned source and original path | Local role / adaptation |
| --- | --- | --- |
| ponytail | [DietrichGebert/ponytail@2961b52](https://github.com/DietrichGebert/ponytail/tree/2961b52fc1fe6a5a87c7a959a79512e5c2f3ffe5/skills/ponytail) | `.agents/skills/ponytail`; explicit current-task use; no session persistence, hooks or review chain |
| codebase-design | [mattpocock/skills@49dd158](https://github.com/mattpocock/skills/tree/49dd158d1076134a641b33efb035946536778336/skills/engineering/codebase-design) | `.agents/skills/codebase-design`; ownership and interface review without automatic parallel designs |
| visual-explainer | [nicobailon/visual-explainer@33a6b4d](https://github.com/nicobailon/visual-explainer/tree/33a6b4d43958d2d152c9cb13762af802062fade5/plugins/visual-explainer) | `.agents/skills/visual-explainer`; inline HTML/SVG only, no scripts, video, external assets or upload |
| interface-design | [Dammyjay93/interface-design@2f9be32](https://github.com/Dammyjay93/interface-design/tree/2f9be3206855bcb2d1d0af262c8bae25cba6658d/.claude/skills/interface-design) | `.agents/skills/interface-design`; existing Flask/Jinja product UI, current design baseline |
| tdd | [mattpocock/skills@49dd158](https://github.com/mattpocock/skills/tree/49dd158d1076134a641b33efb035946536778336/skills/engineering/tdd) | `.agents/skills/tdd`; adapted after first-stage validation, existing `unittest` |
| taste | [Leonxlnx/taste-skill@b482f7a](https://github.com/Leonxlnx/taste-skill/tree/b482f7a970abb98c4108d4a9f761e458c64cefc8/skills/taste-skill) | Existing reviewed wrapper at `.agents/skills/taste`; explicit public pages only |
| apple-hig-reference | Original project text, [Apple HIG](https://developer.apple.com/design/human-interface-guidelines/) consulted 2026-10-14 | `.agents/skills/apple-hig-reference`; optional secondary web UI lens, no copied upstream package or executable assets |

Each imported skill retains an upstream MIT license in its directory.
Selected source trees were inspected at the revisions above without executing
their scripts. Skillguard scans of the selected Ponytail, Codebase Design, TDD
and Interface Design source directories reported 0 active findings. The full
Visual Explainer plugin scan found one active warning in optional
`references/video.md` about transmitting an ElevenLabs key; that reference
and all executable video assets were excluded from the HTML-only adaptation.
A clean scan is not a security certificate.

## Activation and owners

- `database` activates for schema, migration and query work; not routine UI.
  `app/models.py`, migrations and the system contract own facts. Verify the
  affected migration or query on isolated data.
- `ml-models` activates for feature, model and calibration work; not ordinary
  bet-entry work. Feature builders, model code and ML validation runbooks own
  facts. Verify the relevant feature contract and validation gate.
- `deployment-ci` activates for CI and releases; not local documentation
  cleanup. Workflow files and runbooks own facts. Run required gates before
  integration and report the exact result.
- `definition-of-done` activates for actual UI edits; not this documentation
  task. The UI baseline and affected screens own checks. Test affected flows and
  broaden coverage for shared changes.
- `taste` is explicit for public pages. `interface-design` is explicit for
  product screens. `apple-hig-reference` is a secondary lens on explicit
  request. Do not activate these for non-UI work.
- `ponytail`, `codebase-design`, `visual-explainer` and `tdd` are optional,
  explicit skills. They do not install hooks, delegates or new runtimes.

## Compatibility and history

`.claude/skills` retains a compatibility entrypoint for the separate Claude
host. It can expose duplicate names if a client discovers both directories;
actual discovery must be checked in that client. The old Sheet agents remain
as historical files with non-invocation descriptions. The unique single-writer,
UI contract, truthful-copy and accessibility rules are carried by `AGENTS.md`,
`definition-of-done`, `docs/ui_v1_baseline.md` and the architecture contract.
Historical `docs/superpowers/` plans are records, not active workflow rules.

## Cleanup inventory

The only removed tracked entrypoint is `.agents/skills/project-layout/SKILL.md`;
its navigation role moved to `ARCHITECTURE.md` and the system contract.
Five identical Claude mirrors were changed to short compatibility pointers,
and `ui-design-rubric` now points to the current UI skills. Seven Sheet
profiles remain in place for host compatibility but have historical,
non-invocation descriptions and short compatibility bodies. The original
community-stack batch added no executable profile or hook; a subsequent owner
edit revised the protective hook, as described below.

Counting tracked `SKILL.md` files under `.agents/skills` and
`.claude/skills` gives 12 entrypoints before and 16 after (project-only:
6 before, 10 after for the initial stack batch). The later original Apple
reference adds one more project entrypoint. Initial combined UTF-8 sizes were
33,160 and 18,779 bytes respectively. These are historical repository-file counts, not an actual Codex
discovery count or a billed-token measurement.

| Preserved rule from older guidance | Current owner |
| --- | --- |
| Single writer for shared CSS, shell and UI files | `AGENTS.md` and `definition-of-done` |
| Required controls, live fields, over/under semantics, compact widths | `definition-of-done` and `docs/ui_v1_baseline.md` |
| Contrast, keyboard operation, honest empty/loading/error states | `definition-of-done` and `docs/ui_v1_baseline.md` |
| Probability explanation, stake-specific odds, missing CLV, truthful copy | `definition-of-done` and `docs/ui_v1_baseline.md` |
| Private page indexing and public metadata | `docs/ui_v1_baseline.md` and route/template tests |
| User ownership, CSRF, provider freshness and model gates | System contract, application code and tests |
| unittest and required CI security gates | `AGENTS.md`, `deployment-ci` and workflow files |

The global Ponytail plugin is installed and enabled outside this checkout, with
trusted lifecycle hooks in user configuration. `.codex/config.toml` disables
it for this repository. The owner approved an exact trust entry for this checkout,
which was added to the user configuration without altering other trust entries.
In CLI 0.150.1, `codex plugin list --json` reports `ponytail@ponytail` disabled
when run in this checkout and enabled from `/tmp`; the exact plugin ID matches
the project key. An explicit `-c 'plugins."ponytail@ponytail".enabled=false'`
also reports disabled. This establishes effective plugin status for that CLI
diagnostic and shows project scope. An earlier prompt inventory still listed
both global and local Ponytail skills. It may use a separate discovery surface
or inherited catalog; supported read-only diagnostics do not establish why it
remains visible, whether hooks execute, or the desktop client's behavior.
Do not invoke the global skill. The current task's skill catalog includes the
global Ponytail package while its working directory began outside this repo;
it is not evidence of project-scoped activation. A Superpowers skill catalog is available to this task,
although `codex plugin list` did not list a Superpowers installation and the
fresh normal CLI turn reported no Superpowers skill. Do not invoke it for this
project. Desktop discovery remains unverified. A later owner edit expanded the
protective `PreToolUse` matcher to `Edit|Write|apply_patch` through
`.codex/hooks/protect_edits.py`. Focused hook tests cover its parser; the owner
also reported native denial checks. Other write tools and clients remain
unverified.

Custom project roles and the optional coordinated handoff process are documented in
[coding-workflow.md](coding-workflow.md). They do not add a standing team,
automatic repair loop, or lifecycle hook. The role files and project config
have no model pins. A separate CLI 0.150.1 turn rejected `gpt-6-sol` with the
signed-in ChatGPT account, while a temporary `gpt-5.6-sol` override succeeded.
This does not describe or constrain the current task's model or access.

## Usage reporting

`ccusage` 20.0.31 is installed outside the repository at
`../.tools/ccusage/node_modules/.bin/ccusage`. The official npm tarball
had SHA-256 `9a48b128c14b87eb50ff9a27cbb7c871361ec2a1e165963ff9bae3c1531b198e`.
The package uses a platform binary; its published package had a build script,
so installation used `--ignore-scripts`. `--version` and offline Codex
daily/session JSON commands ran successfully. The parser found 41 days and 195
sessions of retained local logs. This is incomplete coverage of all Codex work.
The estimated API-equivalent cost is not a subscription bill. Keep reports
private and aggregate; never commit raw transcripts or infer zero usage from
missing logs.

## Current-system overview

The source-linked HTML overview is a local artifact at
`../edge-tracker-overview.html`, outside this repository, and is also saved
privately in ChatGPT Library as `edge-tracker-overview.html`. It references the
inspected `601e3a058b3bd7d554561b7d94fbaaf8841836cb` source revision. HTML
parsing passed. A browser visual check was blocked by its local-file URL policy,
so desktop and mobile rendering remain unverified. The overview is not published.

## Review and rollback

The initial stack batch was repository instructions, documentation, skills and
project Codex configuration. Subsequent owner edits on this branch changed the
value detector's empty-score contract, related tests, and the protective hook.
Review and test these as separate logical changes. Revert only verified paths;
do not reset other worktrees or user-wide configuration.

Baseline and after-edit `SECRET_KEY=test python3 -m unittest
tests.test_system_contract -v` each passed six tests. At the time of the
initial review this checkout had no `.venv`, so the application suite, Ruff
and Bandit were not run. Later environment restoration and checks belong in
[change evidence](change-evidence.md). Static skill metadata, explicit-only policy and
relative links were checked. An initial read-only Codex smoke attempt could
not open its user state database inside the filesystem sandbox. Ephemeral,
read-only CLI turns with approved state access then confirmed explicit
`codebase-design` and the local restrictions of `ponytail`,
`interface-design`, `visual-explainer`, `tdd` and `taste`. An
unrelated task returned only `OK` and produced no design artifact. These
checks used `--ignore-user-config` and do not prove that the normal desktop
client resolves all duplicate names the same way. The current task loaded the
project `codebase-design` file by its exact path; the direct-file fallback is
available even when picker discovery is not established. Real hook dispatch
and normal desktop skill selection remain unverified because the supported
interactive paths were blocked in this environment. No denied path was retried.
