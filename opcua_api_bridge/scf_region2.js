const { chromium } = require('C:/Users/Administrator/AppData/Roaming/npm/node_modules/playwright');

(async () => {
  const browser = await chromium.connectOverCDP('http://localhost:9222');
  const context = browser.contexts()[0];
  const page = context.pages()[0];

  await page.waitForTimeout(2000);

  // Debug: find all clickable elements near "地域"
  const debug = await page.evaluate(() => {
    const all = document.querySelectorAll('*');
    const results = [];
    for (const el of all) {
      const text = el.textContent || '';
      if (text.includes('\u5e7f\u5dde') && results.length < 10) {
        results.push({
          tag: el.tagName,
          class: el.className.substring(0, 80),
          id: el.id,
          role: el.getAttribute('role'),
          childrenCount: el.children.length,
          rect: el.getBoundingClientRect()
        });
      }
    }
    return results;
  });
  console.log('Elements with 广州:', JSON.stringify(debug, null, 2));

  // Try clicking the first div that has 地域 as label, then find its sibling/input
  await page.evaluate(() => {
    const all = document.querySelectorAll('*');
    for (const el of all) {
      if (el.childNodes.length === 1 && el.textContent.trim() === '\u5e7f\u5dde') {
        // Try clicking its parent or closest interactive element
        const parent = el.parentElement;
        if (parent) {
          console.log('Parent of 广州:', parent.tagName, parent.className);
          parent.click();
        }
        return;
      }
    }
  });
  await page.waitForTimeout(1500);

  const body = await page.locator('body').innerText();
  const hasShanghai = body.includes('上海');
  console.log('上海 visible after parent click:', hasShanghai);

  if (hasShanghai) {
    const shanghai = page.locator('div').filter({ hasText: /^上海$/ }).first();
    await shanghai.click();
    console.log('Clicked 上海');
  }

  await page.waitForTimeout(1000);
  await page.screenshot({ path: 'scf_region2.png', fullPage: true });

  const finalText = await page.locator('body').innerText();
  const regionIdx = finalText.indexOf('地域');
  console.log('Final region:', finalText.substring(regionIdx, regionIdx + 100));

  await browser.close();
})();
