#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

PY_BIN="${PY_BIN:-$ROOT_DIR/.venv/bin/python}"
WEB_URL="${1:-}"

if [[ ! -x "$PY_BIN" ]]; then
  echo "Python binary not found at $PY_BIN"
  exit 1
fi

echo "== Guardrail: lint =="
"$PY_BIN" -m ruff check .

echo "== Guardrail: security scan =="
"$PY_BIN" -m bandit -q -r app -x tests -ll
git ls-files -z | xargs -0 "$PY_BIN" -m detect_secrets.main hook \
  --disable-plugin KeywordDetector \
  --disable-plugin BasicAuthDetector \
  --disable-plugin HexHighEntropyString \
  --disable-plugin Base64HighEntropyString
"$PY_BIN" scripts/check_secret_history.py
"$PY_BIN" -m pip_audit -r requirements.txt

echo "== Guardrail: test suite + coverage =="
"$PY_BIN" -m coverage run -m unittest discover -s tests -v
"$PY_BIN" -m coverage report --include="app/*" --fail-under=80

if [[ -n "$WEB_URL" ]]; then
  echo "== Guardrail: production smoke =="
  curl -fsS "$WEB_URL/health" >/dev/null
  curl -fsS "$WEB_URL/ready" >/dev/null
  curl -fsS "$WEB_URL/ready/model2" >/dev/null || true
fi

echo "Pre-deploy guardrails passed."
