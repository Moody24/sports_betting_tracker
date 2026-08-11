#!/usr/bin/env node
/*
 * shoot.mjs — screenshot + audit a URL at the three widths this project grades.
 *
 * Usage:
 *   node tools/shoot.mjs <url> [--out DIR] [--prefix NAME] [--full|--viewport]
 *
 * Widths are 1440 / 412 / 320, deliberately:
 *   1440 — the desktop Playwright baseline (playwright.config.ts)
 *    412 — the ONLY mobile width with a Playwright baseline (Pixel 7). Grading
 *          at 390 grades a width nothing else in the repo tests.
 *    320 — the narrowest width the definition-of-done forbids overflow at.
 *
 * Reports, per width: horizontal overflow and axe serious/critical violations.
 * Exits non-zero if any width overflows or has a serious/critical violation, so
 * this is usable as a gate and not just as a camera.
 */
import { chromium } from '@playwright/test';
import AxeBuilder from '@axe-core/playwright';

const argv = process.argv.slice(2);
const url = argv.find((a) => !a.startsWith('--'));
if (!url) {
  console.error('usage: node tools/shoot.mjs <url> [--out DIR] [--prefix NAME] [--viewport]');
  process.exit(2);
}
const flag = (name, fallback) => {
  const i = argv.indexOf(`--${name}`);
  return i === -1 ? fallback : argv[i + 1];
};
const out = flag('out', '.');
const prefix = flag('prefix', 'shot');
const fullPage = !argv.includes('--viewport');

const WIDTHS = [
  { name: 'desktop', width: 1440, height: 900 },
  { name: 'mobile', width: 412, height: 915 },
  { name: 'narrow', width: 320, height: 900 },
];

const browser = await chromium.launch();
let failed = 0;

for (const w of WIDTHS) {
  const ctx = await browser.newContext({
    viewport: { width: w.width, height: w.height },
    deviceScaleFactor: 2,
  });
  const page = await ctx.newPage();
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await page.goto(url, { waitUntil: 'networkidle' });
  await page.evaluate(() => document.fonts.ready);
  await page.waitForTimeout(300);

  const path = `${out}/${prefix}-${w.name}.png`;
  await page.screenshot({ path, fullPage });

  const overflow = await page.evaluate(() => ({
    doc: document.documentElement.scrollWidth,
    win: window.innerWidth,
  }));
  const over = overflow.doc > overflow.win;

  // The default export shape differs between CJS/ESM interop paths.
  const Axe = AxeBuilder.default ?? AxeBuilder;
  const { violations } = await new Axe({ page }).analyze();
  const bad = violations.filter((v) => v.impact === 'serious' || v.impact === 'critical');

  if (over || bad.length) failed++;
  console.log(
    `${w.name.padEnd(8)} ${String(w.width).padStart(4)}px  ` +
      `overflow=${over ? `YES (${overflow.doc}>${overflow.win})` : 'no'}  ` +
      `axe_serious_critical=${bad.length}  -> ${path}`
  );
  for (const v of bad) {
    console.log(`    ${v.id} (${v.impact}) x${v.nodes.length}`);
    console.log(`      ${v.nodes[0].failureSummary?.split('\n').slice(0, 2).join(' ')}`);
  }

  await page.close();
  await ctx.close();
}

await browser.close();
process.exit(failed ? 1 : 0);
