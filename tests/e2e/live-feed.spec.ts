import AxeBuilder from '@axe-core/playwright';
import { expect, Page, test } from '@playwright/test';
import { registerAndLogin } from './helpers/auth';
import { labelFixture } from './helpers/fixture-label';

const snapshot = (revision = 10, score = 99, state = 'current') => ({
  revision, source: 'balldontlie', state, match_date: '2026-10-07',
  games: state === 'disabled' ? [] : [{
    source_event_id: 'balldontlie:123', status: 'in_progress', period: 4, clock: '2:15',
    home: { name: 'Preview Home', abbr: 'HOM', score },
    away: { name: 'Preview Away', abbr: 'AWY', score: 95 },
    start_time: '2026-10-07T23:00:00Z', match_date: '2026-10-07',
  }],
  last_success_at: state === 'disabled' ? null : '2026-10-08T01:22:00Z',
  last_attempt_at: state === 'disabled' ? null : '2026-10-08T01:22:00Z',
  provider_delay: 'Provider delay not guaranteed', poll_interval_seconds: 30,
});

async function installStreamHarness(page: Page, supported = true) {
  await page.addInitScript(({ supported }) => {
    const scope = window as any;
    scope.__scoreStreams = [];
    let hidden = false;
    Object.defineProperty(document, 'hidden', { configurable: true, get: () => hidden });
    scope.__setScoreHidden = (value: boolean) => {
      hidden = value;
      document.dispatchEvent(new Event('visibilitychange'));
    };
    Math.random = () => 0.5;
    class FixtureEventSource {
      url: string;
      readyState = 0;
      closed = false;
      listeners: Record<string, Function[]> = {};
      constructor(url: string) { this.url = url; scope.__scoreStreams.push(this); }
      addEventListener(kind: string, callback: Function) {
        (this.listeners[kind] ||= []).push(callback);
      }
      close() { this.closed = true; this.readyState = 2; }
      emit(kind: string, payload: unknown) {
        if (this.closed) return;
        this.readyState = kind === 'error' ? 0 : 1;
        for (const callback of this.listeners[kind] || []) callback({ data: JSON.stringify(payload) });
      }
    }
    scope.EventSource = supported ? FixtureEventSource : undefined;
  }, { supported });
}

async function emit(page: Page, kind: string, payload: unknown) {
  await page.evaluate(({ kind, payload }) => {
    (window as any).__scoreStreams.at(-1).emit(kind, payload);
  }, { kind, payload });
}

const streamCount = (page: Page) => page.evaluate(() => (window as any).__scoreStreams.length);

test('score stream renders safe provider text, ignores old revisions, and applies explicit resets', async ({ page }, testInfo) => {
  await registerAndLogin(page);
  await installStreamHarness(page);
  await page.route('**/nba/live-feed', route => route.fulfill({ json: { ok: true, data: snapshot() } }));
  await page.goto('/nba/today');
  const root = page.locator('#nba-score-stream');
  await expect(root).toHaveAttribute('data-revision', '10');
  await expect.poll(() => streamCount(page)).toBe(1);
  await emit(page, 'snapshot', snapshot(11, 101));
  await expect(root.locator('.score-stream-score').last()).toHaveText('101');
  await emit(page, 'snapshot', snapshot(9, 700));
  await emit(page, 'snapshot', snapshot(11, 800));
  await expect(root.locator('.score-stream-score').last()).toHaveText('101');
  await emit(page, 'reset', snapshot(1, 88));
  await expect(root).toHaveAttribute('data-revision', '1');
  await expect(root.locator('.score-stream-score').last()).toHaveText('88');
  await emit(page, 'heartbeat', { revision: 1, freshness: { ...snapshot(1, 88), state: 'stale' }, stream: { state: 'connected' } });
  await expect(page.locator('#score-stream-state')).toHaveText('Stale');
  await expect(root.locator('.score-stream-score').last()).toHaveText('88');
  await expect(page.locator('#score-stream-fetched')).toContainText('Last successful fetch');
  await emit(page, 'heartbeat', { revision: 1, freshness: { ...snapshot(1), state: 'unavailable' }, stream: { state: 'connected' } });
  await expect(root.locator('.score-stream-card')).toHaveCount(0);

  const unsafe = snapshot(2, 0);
  unsafe.games[0].home.name = '<img src=x onerror=alert(1)>';
  await emit(page, 'snapshot', unsafe);
  await expect(root).toContainText('<img src=x onerror=alert(1)>');
  await expect(root.locator('img')).toHaveCount(0);
  await expect(root.locator('.score-stream-score').last()).toHaveText('0');

  await emit(page, 'snapshot', snapshot(3, 101));
  for (const width of [320, 375, 768, 1200, 1440]) {
    await page.setViewportSize({ width, height: 900 });
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBe(width);
  }
  await page.setViewportSize(testInfo.project.use.viewport as { width: number; height: number });
  await labelFixture(page);
  await page.screenshot({ path: testInfo.outputPath('score-stream-fixture.png'), fullPage: true });
  const accessibility = await new AxeBuilder({ page }).include('#nba-score-stream').analyze();
  expect(accessibility.violations.filter(item => ['serious', 'critical'].includes(item.impact || ''))).toEqual([]);
});

test('hidden pages release streams and timers, then resume with bounded exponential backoff', async ({ page }) => {
  await registerAndLogin(page);
  await installStreamHarness(page);
  await page.clock.install({ time: new Date('2026-10-07T12:00:00Z') });
  await page.clock.pauseAt(new Date('2026-10-07T12:00:01Z'));
  let requests = 0;
  await page.route('**/nba/live-feed', route => {
    requests += 1;
    return route.fulfill({ json: { ok: true, data: snapshot(4) } });
  });
  await page.goto('/nba/today');
  await expect.poll(() => streamCount(page)).toBe(1);
  await emit(page, 'snapshot', snapshot(4));
  await page.evaluate(() => (window as any).__setScoreHidden(true));
  expect(await page.evaluate(() => (window as any).__scoreStreams[0].closed)).toBe(true);
  await page.clock.runFor(60000);
  expect(requests).toBe(1);
  expect(await streamCount(page)).toBe(1);
  await page.evaluate(() => (window as any).__setScoreHidden(false));
  await expect.poll(() => streamCount(page)).toBe(2);
  expect(await page.evaluate(() => (window as any).__scoreStreams[1].url)).toContain('last_event_id=4');
  await emit(page, 'error', {});
  await page.clock.runFor(999);
  expect(await streamCount(page)).toBe(2);
  await page.clock.runFor(1);
  expect(await streamCount(page)).toBe(3);
  await emit(page, 'error', {});
  await page.clock.runFor(1999);
  expect(await streamCount(page)).toBe(3);
  await page.clock.runFor(1);
  expect(await streamCount(page)).toBe(4);
  for (let attempt = 2; attempt < 7; attempt += 1) {
    await emit(page, 'error', {});
    const delay = Math.min(30000, 1000 * 2 ** attempt);
    const before = await streamCount(page);
    await page.clock.runFor(delay - 1);
    expect(await streamCount(page)).toBe(before);
    await page.clock.runFor(1);
    expect(await streamCount(page)).toBe(before + 1);
  }
  await page.evaluate(() => (window as any).__setScoreHidden(true));
  const beforePause = requests;
  await page.clock.runFor(90000);
  expect(requests).toBe(beforePause);
});

test('JSON fallback polls every 30 seconds and retains an unchanged conditional snapshot', async ({ page }) => {
  await registerAndLogin(page);
  await installStreamHarness(page, false);
  await page.clock.install({ time: new Date('2026-10-07T12:00:00Z') });
  await page.clock.pauseAt(new Date('2026-10-07T12:00:01Z'));
  let requests = 0;
  let conditional = '';
  await page.route('**/nba/live-feed', route => {
    requests += 1;
    if (requests > 1) {
      conditional = route.request().headers()['if-none-match'];
      return route.fulfill({ status: 304 });
    }
    return route.fulfill({ json: { ok: true, data: snapshot() }, headers: { ETag: '"revision-10"' } });
  });
  await page.goto('/nba/today');
  await expect(page.locator('#nba-score-stream')).toHaveAttribute('data-revision', '10');
  await page.clock.runFor(29999);
  expect(requests).toBe(1);
  await page.clock.runFor(1);
  await expect.poll(() => requests).toBe(2);
  expect(conditional).toBe('"revision-10"');
  await expect(page.locator('.score-stream-score').last()).toHaveText('99');
  expect(await streamCount(page)).toBe(0);
});

test('disabled feed holds no SSE slot and session expiry stops retries', async ({ page }) => {
  await registerAndLogin(page);
  await installStreamHarness(page);
  await page.clock.install({ time: new Date('2026-10-07T12:00:00Z') });
  await page.clock.pauseAt(new Date('2026-10-07T12:00:01Z'));
  let requests = 0;
  await page.route('**/nba/live-feed', route => {
    requests += 1;
    if (requests > 1) return route.fulfill({ status: 401, json: { ok: false, error: { code: 'unauthorized' } } });
    return route.fulfill({ json: { ok: true, data: snapshot(0, 0, 'disabled') } });
  });
  await page.goto('/nba/today');
  await expect(page.locator('#score-stream-empty')).toContainText('Free score feed not configured');
  await page.clock.runFor(120000);
  expect(requests).toBe(1);
  expect(await streamCount(page)).toBe(0);
  await page.evaluate(() => (window as any).__setScoreHidden(true));
  await page.evaluate(() => (window as any).__setScoreHidden(false));
  await expect(page.locator('#score-stream-sign-in')).toBeVisible();
  await expect(page.locator('#score-stream-connection')).toContainText('Session ended');
  await page.clock.runFor(120000);
  expect(requests).toBe(2);
  expect(await streamCount(page)).toBe(0);
});

test('heartbeat date rollover removes expired games without requiring a new revision', async ({ page }) => {
  await registerAndLogin(page);
  await installStreamHarness(page);
  let current = snapshot(7);
  await page.route('**/nba/live-feed', route => route.fulfill({ json: { ok: true, data: current } }));
  await page.goto('/nba/today');
  await expect(page.locator('.score-stream-card')).toHaveCount(1);
  await expect.poll(() => streamCount(page)).toBe(1);
  await emit(page, 'snapshot', current);
  current = { ...snapshot(7, 0, 'unavailable'), match_date: '2026-10-08', games: [] };
  await emit(page, 'heartbeat', {
    revision: 7, freshness: { ...current, games: undefined }, stream: { state: 'connected' },
  });
  await expect(page.locator('.score-stream-card')).toHaveCount(0);
  await expect(page.locator('#score-stream-state')).toHaveText('Unavailable');
  await emit(page, 'snapshot', snapshot(7));
  await expect(page.locator('.score-stream-card')).toHaveCount(0);
});
