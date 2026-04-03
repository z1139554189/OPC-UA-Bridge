const { chromium } = require('C:/Users/Administrator/AppData/Roaming/npm/node_modules/playwright');

(async () => {
  const browser = await chromium.connectOverCDP('http://localhost:9222');
  const context = browser.contexts()[0];
  const page = context.pages()[0];

  await page.waitForTimeout(2000);

  // Step 1: Find and click the CORRECT "本地上传zip包" radio (4th code submission radio, index 3)
  const radios = await page.locator('input[type="radio"]').all();
  console.log('Total radios:', radios.length);

  // Click the 4th radio (本地上传zip包) based on parent text matching
  for (const radio of radios) {
    const parentText = await radio.evaluate(el => el.closest('div')?.innerText || '');
    if (parentText.includes('\u672c\u5730\u4e0a\u4f20zip')) {
      await radio.click({ force: true });
      console.log('Clicked radio for zip upload, parent:', parentText.substring(0, 80));
      break;
    }
  }
  await page.waitForTimeout(1000);

  // Step 2: Now find the ZIP file input
  const fileInput = page.locator('input[type="file"]').first();
  if (await fileInput.count() > 0) {
    const inputVisible = await fileInput.isVisible().catch(() => false);
    console.log('File input visible:', inputVisible);
    try {
      await fileInput.setInputFiles('C:\\Users\\Administrator\\WorkBuddy\\20260326125244\\opcua_api_bridge\\cloud\\scf\\opcua_cloud_api.zip');
      console.log('ZIP file set');
    } catch (e) {
      console.log('ZIP set failed:', e.message?.substring(0, 100));
    }
  } else {
    console.log('No file input found!');
  }
  await page.waitForTimeout(2000);

  // Step 3: Check the agreement checkbox
  const agreeCheckbox = page.locator('input[type="checkbox"]').filter({ hasText: '' }).last();
  const checkboxes = await page.locator('input[type="checkbox"]').all();
  console.log('Checkboxes:', checkboxes.length);

  for (const cb of checkboxes) {
    const parentText = await cb.evaluate(el => el.closest('div')?.innerText || '');
    if (parentText.includes('\u8bfb\u5e76\u540c\u610f')) {
      const isChecked = await cb.isChecked();
      if (!isChecked) {
        await cb.click({ force: true });
        console.log('Checked agreement checkbox');
      }
      break;
    }
  }
  await page.waitForTimeout(1000);

  // Step 4: Check if 完成 is enabled
  const doneBtn = page.locator('button').filter({ hasText: /\u5b8c\u6210/i }).first();
  const isDisabled = await doneBtn.isDisabled();
  console.log('Done button disabled:', isDisabled);

  // If still disabled, let's find WHY
  if (isDisabled) {
    // Check all radios' checked states
    const radioStates = await page.evaluate(() => {
      const radios = Array.from(document.querySelectorAll('input[type="radio"]'));
      return radios.map((r, i) => ({ index: i, name: r.name, checked: r.checked, parent: r.closest('div')?.innerText?.substring(0, 60) }));
    });
    console.log('Radio states:', JSON.stringify(radioStates.filter(r => r.parent.includes('\u5728\u7ebf') || r.parent.includes('\u672c\u5730') || r.parent.includes('zip') || r.parent.includes('\u4e0a\u4f20')), null, 2));

    // Check the textarea content (code)
    const textareaContent = await page.evaluate(() => {
      const textareas = Array.from(document.querySelectorAll('textarea'));
      return textareas.map(t => t.value.substring(0, 50));
    });
    console.log('Textarea contents:', textareaContent);

    // Check ZIP input value
    const zipInputVal = await page.evaluate(() => {
      const fileInputs = Array.from(document.querySelectorAll('input[type="file"]'));
      return fileInputs.map(f => ({ name: f.name, value: f.value, files: f.files?.length }));
    });
    console.log('ZIP inputs:', JSON.stringify(zipInputVal));
  }

  await page.screenshot({ path: 'scf_fixed.png', fullPage: true });

  // Step 5: If enabled, click 完成
  if (!await doneBtn.isDisabled()) {
    console.log('Clicking 完成!');
    await doneBtn.click({ force: true });
    await page.waitForTimeout(10000);
    console.log('URL:', page.url());
    await page.screenshot({ path: 'scf_created.png', fullPage: true });
  }

  await browser.close();
})();
