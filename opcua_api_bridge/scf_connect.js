const { chromium } = require('C:/Users/Administrator/AppData/Roaming/npm/node_modules/playwright');

(async () => {
  console.log('Connecting to Chrome...');
  const browser = await chromium.connectOverCDP('http://localhost:9222');
  const context = browser.contexts()[0];
  const pages = context.pages();
  const page = pages.length > 0 ? pages[0] : await context.newPage();

  console.log('Current URL:', page.url());
  console.log('Title:', await page.title());

  await page.goto('https://console.cloud.tencent.com/scf');
  await page.waitForTimeout(5000);

  console.log('After navigation URL:', page.url());
  console.log('Title:', await page.title());
  await page.screenshot({ path: 'scf_login.png', fullPage: true });
  console.log('Screenshot saved');

  await browser.close();
})();
