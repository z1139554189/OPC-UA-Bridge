const { chromium } = require('C:/Users/Administrator/AppData/Roaming/npm/node_modules/playwright');

(async () => {
  const browser = await chromium.connectOverCDP('http://localhost:9222');
  const context = browser.contexts()[0];
  const page = context.pages()[0];

  await page.waitForTimeout(2000);

  // Click "编辑" button for function code
  const editBtn = page.locator('button').filter({ hasText: /\u7f16\u8f91/ }).first();
  const editBtnCount = await page.locator('button').filter({ hasText: /\u7f16\u8f91/ }).count();
  console.log('Edit button count:', editBtnCount);

  if (editBtnCount > 0) {
    await editBtn.click({ force: true });
    console.log('Clicked 编辑');
    await page.waitForTimeout(3000);

    console.log('URL after edit click:', page.url());
    await page.screenshot({ path: 'scf_edit.png', fullPage: true });

    const bodyText = await page.locator('body').innerText();
    console.log('Edit page text (first 2000):', bodyText.substring(0, 2000));
  }

  await browser.close();
})();
