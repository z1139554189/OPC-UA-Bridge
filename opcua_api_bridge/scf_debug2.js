const { chromium } = require('C:/Users/Administrator/AppData/Roaming/npm/node_modules/playwright');

(async () => {
  const browser = await chromium.connectOverCDP('http://localhost:9222');
  const context = browser.contexts()[0];
  const page = context.pages()[0];

  // Find all elements containing "从头开始"
  const allContain = await page.locator('*').filter({ hasText: /从头开始/ }).evaluateAll(els =>
    els.slice(0, 5).map(el => ({ tag: el.tagName, class: el.className?.substring(0, 100), id: el.id, role: el.getAttribute('role') }))
  );
  console.log('Elements containing 从头开始:', JSON.stringify(allContain, null, 2));

  // Try to find by aria-label or title
  const ariaMatch = await page.locator('[aria-label*="从头"], [title*="从头"]').count();
  console.log('Aria matches:', ariaMatch);

  // Try JS click on text
  await page.evaluate(() => {
    const all = document.querySelectorAll('*');
    for (const el of all) {
      if (el.childNodes.length === 1 && el.textContent.trim() === '从头开始') {
        console.log('Found exact match:', el.tagName, el.className, el.id);
        el.click();
        return;
      }
    }
  });

  await page.waitForTimeout(2000);
  const bodyAfter = await page.locator('body').innerText();
  console.log('Body after JS click (first 500):', bodyAfter.substring(0, 500));
  await page.screenshot({ path: 'scf_after_js_click.png', fullPage: true });

  await browser.close();
})();
