const { chromium } = require('C:/Users/Administrator/AppData/Roaming/npm/node_modules/playwright');

(async () => {
  const browser = await chromium.connectOverCDP('http://localhost:9222');
  const context = browser.contexts()[0];
  const page = context.pages()[0];

  console.log('URL:', page.url());
  await page.waitForTimeout(2000);

  // Fill function name
  const nameInput = page.locator('input').filter({ hasAttribute: 'placeholder', hasText: /函数名称|名称|name/i }).first();
  const inputCount = await page.locator('input').count();
  console.log('Input count:', inputCount);

  if (await nameInput.count() > 0) {
    await nameInput.fill('opcua-cloud-api');
    console.log('Filled function name');
  }

  // Wait for form to load fully
  await page.waitForTimeout(2000);
  await page.screenshot({ path: 'scf_form.png', fullPage: true });
  console.log('Form screenshot saved');

  await browser.close();
})();
