import { Page } from '@playwright/test';

export async function labelFixture(page: Page, container = 'body') {
  await page.locator(container).first().evaluate((element) => {
    const label = document.createElement('div');
    label.textContent = 'QA fixture — simulated market data';
    label.setAttribute('data-qa-fixture', 'true');
    Object.assign(label.style, { padding: '10px 16px', background: '#3d3013', color: '#ffe1a1', font: '600 12px Arial, sans-serif', borderBottom: '1px solid #795f28', position: 'relative', zIndex: '1' });
    element.prepend(label);
  });
}
