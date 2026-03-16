# UX + Ops Playbook

This playbook pairs UI quality checks with production verification.

## 1) Pre-deploy guardrails

Run:

```bash
scripts/predeploy_guardrails.sh
```

Optional smoke against deployed URL:

```bash
scripts/predeploy_guardrails.sh https://sportsbettingtracker-production.up.railway.app
```

## 2) Railway observability pass

Run:

```bash
scripts/railway_observe.sh
```

What to verify:

- `/health` returns `{"status":"healthy"}`
- `/ready` returns DB connected
- `/ready/model2` shows model loadable
- latest web and scheduler deployments are successful
- no repeated tracebacks in recent logs

## 3) UX telemetry events to watch

Key events emitted by frontend:

- `nba_today_refresh_*`
- `prop_analysis_refresh_*`
- `stat_analysis_refresh_*`
- `unified_slip_refresh_*`
- `unified_slip_submit_*`
- `unified_slip_no_games`

Use logs (or future log sink) to monitor event volume and error ratios.

## 4) Release checklist

- CI `test` and `ui_e2e` jobs green
- Latest migration applied (`flask db current` in runtime env)
- Health/readiness endpoints healthy
- Manual sportsbook smoke:
  - NBA Today -> Prop Analysis -> Stat Analysis -> Bet Builder
  - Quick add single prop works
  - Add-to-parlay and parlay checkout flow works
