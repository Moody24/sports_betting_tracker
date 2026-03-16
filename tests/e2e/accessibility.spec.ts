import AxeBuilder from '@axe-core/playwright';
import { expect, test } from '@playwright/test';
import { registerAndLogin } from './helpers/auth';

const auditedPaths = [
  '/nba/today',
  '/nba/analysis',
  '/nba/stat-analysis',
  '/bets/new?current_tab=prop#prop',
];

test.describe('Accessibility Audit', () => {
  for (const path of auditedPaths) {
    test(`no critical axe issues: ${path}`, async ({ page }) => {
      await registerAndLogin(page);
      await page.goto(path);
      const results = await new AxeBuilder({ page }).analyze();
      const critical = results.violations.filter((v) => v.impact === 'critical');
      expect(critical, `Critical a11y violations found on ${path}`).toEqual([]);
    });
  }
});
