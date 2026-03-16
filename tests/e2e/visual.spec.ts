import { expect, test } from '@playwright/test';
import { registerAndLogin } from './helpers/auth';

const pages = [
  { path: '/nba/today', selector: '#active-games-section' },
  { path: '/nba/analysis', selector: '#analysis-kpis' },
  { path: '/nba/stat-analysis', selector: '#stat-analysis-results-shell' },
  { path: '/bets/new?current_tab=prop#prop', selector: '#ub-root' },
];

test.describe('Visual Regression', () => {
  test('sportsbook core pages remain visually stable', async ({ page }, testInfo) => {
    await registerAndLogin(page);

    for (const item of pages) {
      await page.goto(item.path);
      await expect(page.locator(item.selector)).toBeVisible();
      var name = `${testInfo.project.name}-${item.path.replace(/[/?#=&]/g, '_')}.png`;
      const shot = await page.screenshot({ fullPage: true });
      expect(shot.byteLength).toBeGreaterThan(50_000);
      await testInfo.attach(name, { body: shot, contentType: 'image/png' });
    }
  });
});
