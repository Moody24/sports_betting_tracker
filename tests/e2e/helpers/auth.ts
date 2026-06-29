import { expect, Page } from '@playwright/test';

export async function registerAndLogin(page: Page): Promise<{ username: string; password: string }> {
  const stamp = `${Date.now()}_${Math.random().toString(36).slice(2, 8)}`;
  const username = `e2e_user_${stamp}`;
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
