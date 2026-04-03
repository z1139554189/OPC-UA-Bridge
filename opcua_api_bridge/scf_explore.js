const { chromium } = require('C:/Users/Administrator/AppData/Roaming/npm/node_modules/playwright');

(async () => {
  const browser = await chromium.connectOverCDP('http://localhost:9222');
  const context = browser.contexts()[0];
  const page = context.pages()[0];

  await page.goto('https://console.cloud.tencent.com/scf/list?rid=1&ns=default');
  await page.waitForTimeout(3000);

  // Find and click 新建 button
  const newBtn = page.locator('button').filter({ hasText: /新建|创建|new/i }).first();
  const newBtnCount = await page.locator('button').filter({ hasText: /新建|创建|new/i }).count();
  console.log('New button count:', newBtnCount);

  if (newBtnCount > 0) {
    await newBtn.click();
    await page.waitForTimeout(2000);
    console.log('Clicked 新建, URL:', page.url());
    await page.screenshot({ path: 'scf_after_new.png', fullPage: true });
  } else {
    // Try finding by text in divs/spans
    const allText = await page.locator('*').filter({ hasText: /新建|创建函数/i }).allTextContents();
    console.log('Text matches:', allText.slice(0, 10));
    await page.screenshot({ path: 'scf_no_new_btn.png', fullPage: true });
  }

  // Log page structure
  const html = await page.content();
  console.log('Page HTML length:', html.length);
  console.log('Page URL:', page.url());

  await browser.close();
})();
