const { chromium } = require('C:/Users/Administrator/AppData/Roaming/npm/node_modules/playwright');

(async () => {
  const browser = await chromium.connectOverCDP('http://localhost:9222');
  const context = browser.contexts()[0];
  const page = context.pages()[0];

  console.log('Starting URL:', page.url());

  // Step 1: Go to function list, then click 新建
  await page.goto('https://console.cloud.tencent.com/scf/list-create?rid=1&ns=default', { waitUntil: 'domcontentloaded', timeout: 60000 });
  await page.waitForTimeout(3000);

  // Step 2: Click "从头开始" via JS
  await page.evaluate(() => {
    const all = document.querySelectorAll('*');
    for (const el of all) {
      if (el.childNodes.length === 1 && el.textContent.trim() === '从头开始') {
        el.click();
        console.log('Clicked 从头开始');
        return;
      }
    }
  });
  await page.waitForTimeout(2000);

  // Step 3: Check if form is visible
  const hasForm = await page.locator('body').innerText().then(t => t.includes('函数名称'));
  console.log('Form visible:', hasForm);

  if (!hasForm) {
    console.log('Form not visible, trying direct navigation');
    // Maybe try going directly to create page
    await page.goto('https://console.cloud.tencent.com/scf/list-create?rid=4&ns=default', { waitUntil: 'domcontentloaded', timeout: 60000 });
    await page.waitForTimeout(3000);
  }

  // Step 4: Click Web函数
  const radios = await page.locator('input[type="radio"]').all();
  console.log('Radio count:', radios.length);
  // Find the Web函数 radio - it should be near text "Web函数"
  for (const radio of radios) {
    const parent = await radio.evaluateHandle(el => el.closest('div'));
    const parentText = await parent.asElement()?.innerText() || '';
    if (parentText.includes('Web函数')) {
      await radio.click({ force: true });
      console.log('Clicked Web函数 radio');
      break;
    }
  }
  await page.waitForTimeout(500);

  // Step 5: Fill function name - first app-scf-input
  const funcInputs = page.locator('input.app-scf-input');
  const funcCount = await funcInputs.count();
  console.log('Function name inputs:', funcCount);
  if (funcCount > 0) {
    await funcInputs.first().fill('opcua-cloud-api', { force: true });
    console.log('Filled function name');
  }
  await page.waitForTimeout(500);

  // Step 6: Upload ZIP file
  // First click "本地上传zip包" option
  const zipLabel = page.locator('div').filter({ hasText: '本地上传zip包' }).first();
  if (await zipLabel.count() > 0) {
    await zipLabel.click();
    console.log('Clicked 本地上传zip包');
    await page.waitForTimeout(1000);
  }

  // Find file input
  const fileInputs = page.locator('input[type="file"]');
  const fileCount = await fileInputs.count();
  console.log('File inputs:', fileCount);

  if (fileCount > 0) {
    await fileInputs.first().setInputFiles('C:\\Users\\Administrator\\WorkBuddy\\20260326125244\\opcua_api_bridge\\cloud\\scf\\opcua_cloud_api.zip');
    console.log('ZIP uploaded');
    await page.waitForTimeout(3000);
  } else {
    console.log('No file input found, checking for upload trigger');
    // Try clicking a button that might open file dialog
    await page.evaluate(() => {
      const btns = document.querySelectorAll('button, div[role="button"]');
      for (const btn of btns) {
        if (btn.textContent.includes('上传') || btn.textContent.includes('zip')) {
          btn.click();
          console.log('Clicked upload button:', btn.textContent.trim());
          return;
        }
      }
    });
    await page.waitForTimeout(2000);
  }

  // Step 7: Change region to Shanghai
  // Find the region div with "广州"
  const regionDivs = await page.locator('div').filter({ hasText: /^广州$/ }).all();
  console.log('Region divs found:', regionDivs.length);
  if (regionDivs.length > 0) {
    await regionDivs[0].click();
    await page.waitForTimeout(1500);
    // Try to find and click 上海
    const shanghai = page.locator('div').filter({ hasText: /^上海$/ }).first();
    if (await shanghai.count() > 0) {
      await shanghai.click();
      console.log('Changed region to 上海');
    } else {
      // Search in any visible dropdown
      await page.keyboard.type('上海');
      await page.waitForTimeout(1000);
    }
  }
  await page.waitForTimeout(1000);

  // Final screenshot and state check
  const finalText = await page.locator('body').innerText();
  console.log('Final form (函数名称 section):', finalText.substring(finalText.indexOf('函数名称'), finalText.indexOf('函数名称') + 500));

  await page.screenshot({ path: 'scf_final_form.png', fullPage: true });
  console.log('Final screenshot saved');

  await browser.close();
})();
