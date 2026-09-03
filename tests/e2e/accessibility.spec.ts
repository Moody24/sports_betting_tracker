import AxeBuilder from '@axe-core/playwright';
import { expect, test } from '@playwright/test';
import { registerAndLogin } from './helpers/auth';

const publicAuditedPaths = [
  '/',
  '/methodology',
  '/responsible-gambling',
  '/privacy',
  '/terms',
  '/data-sources',
  '/about',
  '/auth/login',
  '/auth/register',
  '/missing-accessibility-fixture',
];

const privateAuditedPaths = [
  '/dashboard',
  '/bets',
  '/nba/today',
  '/nba/analysis',
  '/nba/stat-analysis',
  '/bets/new?current_tab=prop#prop',
];

test.describe('Accessibility Audit', () => {
  for (const path of publicAuditedPaths) {
    test(`no serious or critical axe issues: ${path}`, async ({ page }) => {
      // Freeze fade-in animations before scanning — otherwise axe can catch a
      // staggered card mid-transition and report its transient low-opacity
      // text as a contrast violation even though the settled color passes.
      await page.emulateMedia({ reducedMotion: 'reduce' });
      await page.goto(path);
      const results = await new AxeBuilder({ page }).analyze();
      const blocking = results.violations.filter(
        (violation) => violation.impact === 'critical' || violation.impact === 'serious',
      );
      expect(blocking, `Serious or critical a11y violations found on ${path}`).toEqual([]);
    });
  }

  for (const path of privateAuditedPaths) {
    test(`no serious or critical axe issues: ${path}`, async ({ page }) => {
      await page.emulateMedia({ reducedMotion: 'reduce' });
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
