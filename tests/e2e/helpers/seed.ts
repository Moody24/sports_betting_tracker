import { execFileSync } from 'node:child_process';
import { E2E_SECRET_KEY, E2E_DATABASE_URL, E2E_RATELIMIT_ENABLED } from './env';

/**
 * Seeds a deterministic set of resolved/pending/live-trackable bets for an
 * already-registered user via `flask e2e-seed-bets`, so visual baselines can
 * cover populated states instead of only the empty state.
 */
export function seedBets(username: string): void {
  execFileSync('./.venv/bin/flask', ['e2e-seed-bets', '--username', username], {
    env: {
      ...process.env,
      SECRET_KEY: E2E_SECRET_KEY,
      DATABASE_URL: E2E_DATABASE_URL,
      RATELIMIT_ENABLED: E2E_RATELIMIT_ENABLED,
    },
    stdio: 'pipe',
  });
}
