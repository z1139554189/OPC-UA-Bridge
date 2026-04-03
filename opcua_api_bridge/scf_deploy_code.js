const { chromium } = require('C:/Users/Administrator/AppData/Roaming/npm/node_modules/playwright');

(async () => {
  const browser = await chromium.connectOverCDP('http://localhost:9222');
  const context = browser.contexts()[0];
  const page = context.pages()[0];

  console.log('Current URL:', page.url());
  await page.waitForTimeout(5000);

  // Check deployment status
  const bodyText = await page.locator('body').innerText();
  console.log('Page text (first 2000):', bodyText.substring(0, 2000));

  // Check for upload code option
  const hasUpload = bodyText.includes('\u4e0a\u4f20\u4ee3\u7801') || bodyText.includes('\u66f4\u6362\u4ee3\u7801');
  console.log('Has upload code option:', hasUpload);

  await page.screenshot({ path: 'scf_detail.png', fullPage: true });

  await browser.close();
})();
