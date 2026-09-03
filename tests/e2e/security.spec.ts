import { expect, test } from '@playwright/test';
import { registerAndLogin } from './helpers/auth';

test.describe('Session security', () => {
  test('logout clears current and legacy parlay storage before another login', async ({ page }) => {
    await registerAndLogin(page);
    await page.evaluate(() => {
      sessionStorage.setItem('sbt_parlay_queue_v1', '[{"player":"Private Player"}]');
      sessionStorage.setItem('parlayQueue', '[{"legacy":true}]');
      localStorage.setItem('parlayQueue', '[{"legacy":true}]');
    });

    await page.locator('.user-btn').click();
    await page.getByRole('button', { name: 'Sign Out' }).click();
    await expect(page).toHaveURL(/\/auth\/login/);

    const storage = await page.evaluate(() => ({
      current: sessionStorage.getItem('sbt_parlay_queue_v1'),
      legacySession: sessionStorage.getItem('parlayQueue'),
      legacyLocal: localStorage.getItem('parlayQueue'),
    }));
    expect(storage).toEqual({ current: null, legacySession: null, legacyLocal: null });

    await registerAndLogin(page);
    expect(await page.evaluate(() => sessionStorage.getItem('sbt_parlay_queue_v1'))).toBeNull();
  });
});
