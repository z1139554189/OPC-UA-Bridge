const { chromium } = require('C:/Users/Administrator/AppData/Roaming/npm/node_modules/playwright');

(async () => {
  const browser = await chromium.connectOverCDP('http://localhost:9222');
  const context = browser.contexts()[0];
  const page = context.pages()[0];

  await page.waitForTimeout(2000);

  // Use JS to find and click the region dropdown
  const result = await page.evaluate(() => {
    const allDivs = document.querySelectorAll('div');
    for (const div of allDivs) {
      if (div.childNodes.length === 1 && div.textContent.trim() === '\u5e7f\u5dde') {
        console.log('Found region div:', div.className, 'rect:', div.getBoundingClientRect());
        div.click();
        return { found: true, className: div.className };
      }
    }
    return { found: false };
  });
  console.log('Result:', result);
  await page.waitForTimeout(1500);

  // Check for Shanghai in dropdown
  const bodyText = await page.locator('body').innerText();
  const hasShanghai = bodyText.includes('\u4e0a\u6d77') || bodyText.includes('上海');
  console.log('Shanghai visible in body:', hasShanghai);

  // Try to find Shanghai option in any dropdown
  const shanghaiEl = page.locator('div').filter({ hasText: /\u4e0a\u6d77/ }).first();
  if (await shanghaiEl.count() > 0) {
    await shanghaiEl.click();
    console.log('Clicked Shanghai');
  }

  await page.waitForTimeout(1000);
  await page.screenshot({ path: 'scf_region.png', fullPage: true });

  const finalText = await page.locator('body').innerText();
  const regionIdx = finalText.indexOf('\u5730\u57df');
  console.log('Region section:', finalText.substring(regionIdx, regionIdx + 200));

  await browser.close();
})();
