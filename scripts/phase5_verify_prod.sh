#!/usr/bin/env bash
set -euo pipefail

# Phase 5 production verification for split Railway services:
# - web service handles requests (scheduler disabled)
# - scheduler service runs APScheduler jobs

if ! command -v railway >/dev/null 2>&1; then
  echo "railway CLI not found. Install it first."
  exit 1
fi

if ! command -v curl >/dev/null 2>&1; then
  echo "curl not found."
  exit 1
fi

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
FLASK_BIN="${REPO_ROOT}/.venv/bin/flask"
WEB_URL="${1:-https://sportsbettingtracker-production.up.railway.app}"

if [[ ! -x "${FLASK_BIN}" ]]; then
  echo "Missing ${FLASK_BIN}. Create/install the repo virtualenv first."
  exit 1
fi

echo "== Phase 5: Web Health =="
curl -fsS "${WEB_URL}/health" && echo
curl -fsS "${WEB_URL}/ready" && echo
curl -fsS "${WEB_URL}/ready/model2" && echo

echo
echo "== Phase 5: Service Config Guards =="
railway variable list --service sports_betting_tracker --environment production | rg "SCHEDULER_ENABLED|WEB_CONCURRENCY|MIGRATION_MAX_SECONDS"
railway variable list --service scheduler --environment production | rg "SCHEDULER_ENABLED|WEB_CONCURRENCY|MIGRATION_MAX_SECONDS"

echo
echo "== Phase 5: Latest Deployments =="
railway deployment list --service sports_betting_tracker --environment production | head -n 4
railway deployment list --service scheduler --environment production | head -n 4

echo
echo "== Phase 5: Scheduler Production Readiness =="
(
  cd "${REPO_ROOT}"
  railway run --service scheduler --environment production --no-local \
    "${FLASK_BIN}" --app run.py prod-readiness
)

echo
echo "== Phase 5: Recent Scheduler Jobs (top 20) =="
(
  cd "${REPO_ROOT}"
  railway run --service scheduler --environment production --no-local \
    "${FLASK_BIN}" --app run.py model_status | awk '
      /^=== Recent JobLog entries ===/ {print; in_block=1; next}
      in_block && /^- / {print; count++; if (count >= 20) exit}
    '
)

echo
echo "Phase 5 verification completed."
