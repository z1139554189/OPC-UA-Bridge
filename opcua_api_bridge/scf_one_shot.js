const { chromium } = require('C:/Users/Administrator/AppData/Roaming/npm/node_modules/playwright');

(async () => {
  const browser = await chromium.connectOverCDP('http://localhost:9222');
  const context = browser.contexts()[0];
  const page = context.pages()[0];

  // Fresh navigation
  await page.goto('https://console.cloud.tencent.com/scf/list-create?rid=4&ns=default', { timeout: 60000 });
  await page.waitForTimeout(5000);

  // Step 1: Click 从头开始 via JS
  await page.evaluate(() => {
    const all = document.querySelectorAll('*');
    for (const el of all) {
      if (el.childNodes.length === 1 && el.textContent.trim() === '\u4ece\u5934\u5f00\u59cb') {
        el.click();
        console.log('Clicked \u4ece\u5934\u5f00\u59cb');
        return;
      }
    }
  });
  await page.waitForTimeout(3000);

  // Check if we're on the blank template form
  const bodyText = await page.locator('body').innerText();
  const hasForm = bodyText.includes('\u51fd\u6570\u540d\u79f0');
  console.log('Blank form loaded:', hasForm);

  // Get all radio buttons
  const radios = await page.locator('input[type="radio"]').all();
  console.log('Radios:', radios.length);

  // Click Web函数 (the second radio in the function type group)
  // Function type radios are usually in a specific area
  for (const radio of radios) {
    const parent = await radio.evaluateHandle(el => el.closest('div').innerText);
    const parentText = await parent.asElement()?.innerText() || '';
    if (parentText.includes('Web')) {
      await radio.click({ force: true });
      console.log('Clicked Web函数 radio');
      break;
    }
  }
  await page.waitForTimeout(500);

  // Fill function name
  const funcInputs = page.locator('input.app-scf-input');
  await funcInputs.first().fill('opcua-cloud-api', { force: true });
  console.log('Filled function name');
  await page.waitForTimeout(500);

  // Check if Shanghai region is selected (rid=4 should be Shanghai)
  const regionText = await page.locator('body').innerText();
  const regionIdx = regionText.indexOf('\u5730\u57df');
  console.log('Region section:', regionText.substring(regionIdx, regionIdx + 100));

  // Click 本地上传zip包
  const zipDiv = page.locator('div').filter({ hasText: '\u672c\u5730\u4e0a\u4f20zip\u5305' }).first();
  if (await zipDiv.count() > 0) {
    await zipDiv.click({ force: true });
    console.log('Clicked zip option');
    await page.waitForTimeout(1000);
  }

  // Upload ZIP
  const fileInput = page.locator('input[type="file"]');
  if (await fileInput.count() > 0) {
    await fileInput.first().setInputFiles('C:\\Users\\Administrator\\WorkBuddy\\20260326125244\\opcua_api_bridge\\cloud\\scf\\opcua_cloud_api.zip');
    console.log('ZIP uploaded');
    await page.waitForTimeout(3000);
  } else {
    console.log('No file input found!');
  }

  // Scroll down to find 完 button
  await page.evaluate(() => window.scrollTo(0, document.body.scrollHeight));
  await page.waitForTimeout(1000);

  // Try to click 完 with force
  const doneBtn = page.locator('button').filter({ hasText: /\u5b8c\u6210|\u521b\u5efa/i }).first();
  const doneBtnText = await doneBtn.innerText().catch(() => 'not found');
  console.log('Done button text:', doneBtnText);

  // Get button disabled state
  const isDisabled = await doneBtn.isDisabled().catch(() => 'unknown');
  console.log('Done button disabled:', isDisabled);

  await page.screenshot({ path: 'scf_before_submit.png', fullPage: true });

  if (!isDisabled) {
    await doneBtn.click({ force: true });
    console.log('Clicked 完成');
    await page.waitForTimeout(10000);
    console.log('URL after submit:', page.url());
    await page.screenshot({ path: 'scf_after_submit.png', fullPage: true });
  }

  await browser.close();
})();
