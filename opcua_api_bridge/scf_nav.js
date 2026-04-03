const { chromium } = require('C:/Users/Administrator/AppData/Roaming/npm/node_modules/playwright');

(async () => {
  const browser = await chromium.connectOverCDP('http://localhost:9222');
  const context = browser.contexts()[0];
  const page = context.pages().find(p => p.url().includes('console.cloud.tencent.com')) || context.pages()[0];
  const mainPage = page;

  console.log('Current URL:', mainPage.url());

  // Navigate to SCF console
  await mainPage.goto('https://console.cloud.tencent.com/scf');
  await mainPage.waitForTimeout(3000);
  console.log('After goto SCF URL:', mainPage.url());
  console.log('Title:', await mainPage.title());
  await mainPage.screenshot({ path: 'scf_main.png', fullPage: true });
  console.log('SCF main screenshot saved');

  await browser.close();
})();
