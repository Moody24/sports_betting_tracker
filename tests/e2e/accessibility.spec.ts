import AxeBuilder from '@axe-core/playwright';
import { expect, test } from '@playwright/test';
import { registerAndLogin } from './helpers/auth';

const auditedPaths = [
  '/dashboard',
  '/bets',
  '/nba/today',
  '/nba/analysis',
  '/nba/stat-analysis',
  '/bets/new?current_tab=prop#prop',
];

test.describe('Accessibility Audit', () => {
  for (const path of auditedPaths) {
    test(`no serious or critical axe issues: ${path}`, async ({ page }) => {
      await registerAndLogin(page);
      await page.goto(path);
      const results = await new AxeBuilder({ page }).analyze();
      const blocking = results.violations.filter(
        (violation) => violation.impact === 'critical' || violation.impact === 'serious',
      );
      expect(blocking, `Serious or critical a11y violations found on ${path}`).toEqual([]);
    });
  }
});
