# Local Codex handoff eval

This developer-only DeepEval check compares a structured handoff with
independently captured facts. It does not run the application, read raw Codex
transcripts, call an LLM judge, or prove general hallucination resistance.
The current sample is a manual normalization of one fresh, read-only CLI turn;
its negative control intentionally invents a test count and role discovery.

DeepEval 4.2.8 (Apache-2.0) is installed in the isolated sibling directory
`../.tools/deepeval/.venv` on this task's Mac. The official PyPI wheel's
SHA-256 was verified as
`1bc6ec6ad9f36427e5578a01b4a2aaa357d7ac51bb0cd726af7315304b0339f8`;
an installed dependency snapshot is at
`../.tools/deepeval/installed-2026-10-14.txt`. It is not an application
dependency. Use a private temporary home and strip inherited credentials when
running it:

```sh
mkdir -p /tmp/edge-deepeval-home
env -i HOME=/tmp/edge-deepeval-home PATH=/usr/bin:/bin \
  DEEPEVAL_TELEMETRY_OPT_OUT=1 DEEPEVAL_DISABLE_DOTENV=1 \
  DEEPEVAL_DISABLE_LEGACY_KEYFILE=1 CONFIDENT_OPEN_BROWSER=0 \
  ../.tools/deepeval/.venv/bin/python tools/evals/check_handoff.py
```

The runner rejects missing privacy settings and several common model/API key
variables. The `env -i` launcher removes all inherited credentials. Built-in
judge metrics are deliberately absent: many call external services by default.
The default command exits 0 for the normalized observed sample and internal
negative control. Adding `--negative-only` deliberately makes DeepEval reject
the false report with exit 1.
To evaluate future work, capture a small structured report and independent
command outcomes, then add a distinct sample. Keep transcripts and secrets out
of the repository. Record both successful and deliberately false cases, and
state how many real agent responses were evaluated.
