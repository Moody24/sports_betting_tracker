import { expect, Page } from '@playwright/test';

export async function registerAndLogin(page: Page): Promise<{ username: string; password: string }> {
  const randomPart = Math.random().toString(36).slice(2, 8);
  const timePart = Date.now().toString().slice(-6);
  // Keep entropy inside the form's 20-character username limit. Putting the
  // random suffix last caused parallel workers to submit identical truncations.
  const username = `e2e_${randomPart}_${timePart}`;
  const password = 'password123';
  const email = `${username}@example.com`;

  await page.goto('/auth/register');
  await page.locator('#username').fill(username);
  await page.locator('#email').fill(email);
  await page.locator('#password').fill(password);
  await page.locator('#confirm_password').fill(password);
  await page.locator('form [type="submit"]').click();
  await expect(page).toHaveURL(/\/auth\/login/);

  await page.locator('#username').fill(username);
  await page.locator('#password').fill(password);
  await page.locator('form [type="submit"]').click();
  await expect(page).toHaveURL(/\/$/);
  await expect(page.locator('text=Login successful')).toBeVisible();

  return { username, password };
}
