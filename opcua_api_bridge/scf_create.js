const { chromium } = require('C:/Users/Administrator/AppData/Roaming/npm/node_modules/playwright');

(async () => {
  const browser = await chromium.connectOverCDP('http://localhost:9222');
  const context = browser.contexts()[0];
  const page = context.pages()[0];

  console.log('Current URL:', page.url());

  // Click "函数服务" in left sidebar
  const sidebarLinks = await page.locator('a, div[role="button"], span').filter({ hasText: '函数服务' }).all();
  console.log('Found sidebar items:', sidebarLinks.length);

  // Try clicking by href pattern
  await page.goto('https://console.cloud.tencent.com/scf/list?rid=1&ns=default');
  await page.waitForTimeout(3000);
  console.log('Function list URL:', page.url());
  console.log('Title:', await page.title());
  await page.screenshot({ path: 'scf_list.png', fullPage: true });
  console.log('Function list screenshot saved');

  await browser.close();
})();
