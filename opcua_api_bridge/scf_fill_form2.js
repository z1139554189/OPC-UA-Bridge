const { chromium } = require('C:/Users/Administrator/AppData/Roaming/npm/node_modules/playwright');

(async () => {
  const browser = await chromium.connectOverCDP('http://localhost:9222');
  const context = browser.contexts()[0];
  const page = context.pages()[0];

  await page.waitForTimeout(2000);

  // 1. Click "Web函数" (Web Function) instead of "事件函数"
  const webFunc = page.locator('div').filter({ hasText: /^Web函数/ }).first();
  if (await webFunc.count() > 0) {
    await webFunc.click();
    console.log('Clicked Web函数');
  }
  await page.waitForTimeout(500);

  // 2. Find and fill function name input
  // Look for input near "函数名称" label
  const inputs = await page.locator('input[type="text"]').all();
  console.log('Text inputs found:', inputs.length);

  // Fill the first empty text input (should be function name)
  for (const input of inputs) {
    const placeholder = await input.getAttribute('placeholder');
    const value = await input.inputValue();
    console.log('Input placeholder:', placeholder, 'value:', value);
    if (!value && placeholder !== '支持通过实例ID') {
      await input.fill('opcua-cloud-api');
      console.log('Filled function name');
      break;
    }
  }

  // 3. Check current region - click to change to Shanghai
  // Region dropdown shows "广州" currently, need to change to 上海
  const regionDiv = page.locator('div').filter({ hasText: /^地域$/ }).locator('..').locator('div').last();
  const regionText = await regionDiv.innerText().catch(() => 'not found');
  console.log('Region div text:', regionText);

  await page.waitForTimeout(1000);
  await page.screenshot({ path: 'scf_form_filled.png', fullPage: true });
  console.log('Screenshot saved');

  // Get full form text for debugging
  const formText = await page.locator('body').innerText();
  const formSection = formText.substring(formText.indexOf('函数名称'), formText.indexOf('触发器配置'));
  console.log('Form section:', formSection.substring(0, 1000));

  await browser.close();
})();
