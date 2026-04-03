const { chromium } = require('C:/Users/Administrator/AppData/Roaming/npm/node_modules/playwright');

(async () => {
  const browser = await chromium.connectOverCDP('http://localhost:9222');
  const context = browser.contexts()[0];
  const page = context.pages()[0];

  await page.waitForTimeout(3000);

  // 1. Click "Web函数" radio (2nd radio, index 1)
  const radios = await page.locator('input[type="radio"]').all();
  console.log('Radio count:', radios.length);

  // Click the Web函数 radio (2nd one, at top ~482)
  const webFuncRadio = radios[1];
  await webFuncRadio.click({ force: true });
  console.log('Clicked Web函数 radio');
  await page.waitForTimeout(500);

  // 2. Fill function name - use JS to find the correct input
  await page.evaluate(() => {
    const inputs = document.querySelectorAll('input.app-scf-input');
    // Find the one that's near "函数名称" text and empty
    inputs[0]?.focus();
    inputs[0]?.click();
  });
  await page.waitForTimeout(200);

  const funcNameInput = page.locator('input.app-scf-input').first();
  await funcNameInput.click({ force: true });
  await funcNameInput.fill('opcua-cloud-api', { force: true });
  console.log('Filled function name');
  await page.waitForTimeout(500);

  // 3. Check current state - look at the region and runtime
  const bodyText = await page.locator('body').innerText();
  const regionIdx = bodyText.indexOf('地域');
  const runtimeIdx = bodyText.indexOf('运行环境');
  console.log('Region section:', bodyText.substring(regionIdx, regionIdx + 200));
  console.log('Runtime section:', bodyText.substring(runtimeIdx, runtimeIdx + 200));

  await page.screenshot({ path: 'scf_form_step1.png', fullPage: true });
  console.log('Screenshot saved');

  await browser.close();
  console.log('Done');
})();
