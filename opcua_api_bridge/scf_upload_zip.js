const { chromium } = require('C:/Users/Administrator/AppData/Roaming/npm/node_modules/playwright');

(async () => {
  const browser = await chromium.connectOverCDP('http://localhost:9222');
  const context = browser.contexts()[0];
  const page = context.pages()[0];

  await page.waitForTimeout(3000);

  // 1. Change region to Shanghai - find the region dropdown
  // Click the region text "广州"
  const regionEl = page.locator('div').filter({ hasText: /^广州$/ }).first();
  if (await regionEl.count() > 0) {
    await regionEl.click();
    console.log('Clicked 地域 dropdown');
    await page.waitForTimeout(1000);

    // Look for Shanghai option
    const shanghaiEl = page.locator('div').filter({ hasText: /^上海$/ }).first();
    if (await shanghaiEl.count() > 0) {
      await shanghaiEl.click();
      console.log('Selected 上海');
    } else {
      // Try to find in a dropdown list
      const allVisible = await page.locator('div[style*="overflow"]:visible, .ivu-select-dropdown:visible').last().innerText().catch(() => '');
      console.log('Dropdown content:', allVisible.substring(0, 300));
    }
  }
  await page.waitForTimeout(500);

  // 2. Switch to "本地上传zip包" instead of "在线编辑"
  // Find and click "本地上传zip包"
  const zipOpt = page.locator('div').filter({ hasText: '本地上传zip包' }).first();
  if (await zipOpt.count() > 0) {
    await zipOpt.click();
    console.log('Clicked 本地上传zip包');
    await page.waitForTimeout(1000);
  }

  // 3. Upload the ZIP file using file chooser
  const zipPath = 'C:\\Users\\Administrator\\WorkBuddy\\20260326125244\\opcua_api_bridge\\cloud\\scf\\opcua_cloud_api.zip';
  const fileInput = page.locator('input[type="file"]').first();
  if (await fileInput.count() > 0) {
    await fileInput.setInputFiles(zipPath);
    console.log('ZIP file set');
    await page.waitForTimeout(2000);
  } else {
    console.log('No file input found');
  }

  // Check the current form state
  const bodyText = await page.locator('body').innerText();
  console.log('Current form (first 1500):', bodyText.substring(0, 1500));

  await page.screenshot({ path: 'scf_form_zip.png', fullPage: true });
  console.log('Screenshot saved');

  await browser.close();
})();
