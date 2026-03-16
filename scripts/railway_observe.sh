#!/usr/bin/env bash
set -euo pipefail

WEB_URL="${1:-https://sportsbettingtracker-production.up.railway.app}"

if ! command -v railway >/dev/null 2>&1; then
  echo "railway CLI not found."
  exit 1
fi

echo "== Health endpoints =="
curl -fsS "$WEB_URL/health"
echo
curl -fsS "$WEB_URL/ready"
echo
curl -fsS "$WEB_URL/ready/model2" || true
echo

echo "== Deployments (web) =="
railway deployment list --service sports_betting_tracker --environment production | head -n 6
echo

echo "== Deployments (scheduler) =="
railway deployment list --service scheduler --environment production | head -n 6
echo

echo "== Recent logs (web) =="
railway logs --service sports_betting_tracker --environment production --lines 80 || true
echo

echo "== Recent logs (scheduler) =="
railway logs --service scheduler --environment production --lines 80 || true
