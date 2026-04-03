const { chromium } = require('C:/Users/Administrator/AppData/Roaming/npm/node_modules/playwright');

(async () => {
  const browser = await chromium.connectOverCDP('http://localhost:9222');
  const context = browser.contexts()[0];
  const page = context.pages()[0];

  console.log('URL:', page.url());
  await page.waitForTimeout(2000);

  // Click "从头开始"
  const fromScratch = page.locator('div').filter({ hasText: '从头开始' }).first();
  console.log('Found 从头开始:', await fromScratch.count());
  await fromScratch.click();
  await page.waitForTimeout(2000);

  // Check what's visible now
  const newBodyText = await page.locator('body').innerText();
  console.log('After clicking 从头开始 (first 2000):', newBodyText.substring(0, 2000));

  await page.screenshot({ path: 'scf_from_scratch.png', fullPage: true });
  console.log('Screenshot saved');

  await browser.close();
})();
