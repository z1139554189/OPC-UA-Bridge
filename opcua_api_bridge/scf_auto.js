const { chromium } = require('playwright');

(async () => {
  const browser = await chromium.connectOverCDP('http://localhost:9222');
  const context = browser.contexts()[0] || await browser.newContext();
  const page = context.pages()[0] || await context.newPage();

  await page.goto('https://console.tencentcloud.com/scf');
  await page.waitForTimeout(3000);

  const title = await page.title();
  console.log('Page title:', title);

  const url = page.url();
  console.log('Current URL:', url);

  await page.screenshot({ path: 'scf_console.png', fullPage: true });
  console.log('Screenshot saved to scf_console.png');

  await browser.close();
})();
