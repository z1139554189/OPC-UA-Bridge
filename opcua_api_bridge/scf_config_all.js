const { chromium } = require('C:/Users/Administrator/AppData/Roaming/npm/node_modules/playwright');

(async () => {
  const browser = await chromium.connectOverCDP('http://localhost:9222');
  const context = browser.contexts()[0];
  const page = context.pages()[0];

  await page.waitForTimeout(2000);

  // 1. Add environment variables via 导入 button
  const importBtn = page.locator('button').filter({ hasText: /\u5bfc\u5165/ }).first();
  if (await importBtn.count() > 0) {
    await importBtn.click({ force: true });
    console.log('Clicked 导入');
    await page.waitForTimeout(1000);
  }

  // Fill textarea
  const textarea = page.locator('textarea').first();
  if (await textarea.count() > 0) {
    await textarea.fill('DB_HOST=sh-cynosdbmysql-grp-4f512ckw.sql.tencentcdb.com\nDB_PORT=21397\nDB_NAME=opcua_db\nDB_USER=opcua_user\nDB_PASSWORD=Admin_00', { force: true });
    console.log('Filled env vars');
    await textarea.dispatchEvent('input');
  }
  await page.waitForTimeout(500);

  // 2. Enable VPC - find and check 私有网络 checkbox
  const vpcCheck = await page.evaluate(() => {
    const all = Array.from(document.querySelectorAll('*'));
    for (const el of all) {
      if (el.textContent.includes('\u79c1\u6709\u7f51\u7edc') && el.textContent.includes('\u542f\u7528')) {
        const cb = el.querySelector('input[type="checkbox"]');
        if (cb && !cb.checked) {
          cb.click();
          return 'clicked 私有网络 checkbox';
        }
        if (cb && cb.checked) return 'already checked';
      }
    }
    return 'not found';
  });
  console.log('VPC check:', vpcCheck);
  await page.waitForTimeout(1000);

  // 3. Change code submission to ZIP upload
  // Find the ZIP radio button
  const zipResult = await page.evaluate(() => {
    const radios = Array.from(document.querySelectorAll('input[type="radio"]'));
    for (const r of radios) {
      const parent = r.closest('div');
      if (parent && parent.innerText.includes('\u672c\u5730\u4e0a\u4f20zip')) {
        if (!r.checked) {
          r.click();
          return 'clicked zip radio';
        }
        return 'zip already selected';
      }
    }
    return 'zip radio not found';
  });
  console.log('ZIP radio:', zipResult);
  await page.waitForTimeout(1000);

  // 4. Upload ZIP file
  const fileInput = page.locator('input[type="file"]');
  if (await fileInput.count() > 0) {
    try {
      await fileInput.first().setInputFiles('C:\\Users\\Administrator\\WorkBuddy\\20260326125244\\opcua_api_bridge\\cloud\\scf\\opcua_cloud_api.zip');
      console.log('ZIP uploaded');
    } catch (e) {
      console.log('ZIP upload failed:', e.message?.substring(0, 100));
    }
  } else {
    console.log('No file input');
  }
  await page.waitForTimeout(2000);

  // 5. Increase execution timeout
  const timeoutInput = await page.evaluate(() => {
    // Find timeout inputs (should be around 3 seconds)
    const inputs = Array.from(document.querySelectorAll('input'));
    for (const inp of inputs) {
      const parent = inp.closest('div');
      if (parent && parent.innerText.includes('\u6267\u884c\u8d85\u65f6\u65f6\u95f4')) {
        const val = parent.innerText.match(/(\d+)\s*\u79d2/);
        if (val) {
          inp.fill('30');
          return 'set timeout to 30s';
        }
      }
    }
    return 'timeout not found';
  });
  console.log('Timeout:', timeoutInput);

  await page.waitForTimeout(1000);
  await page.screenshot({ path: 'scf_before_save.png', fullPage: true });

  // 6. Click 保存
  const saveResult = await page.evaluate(() => {
    const btns = Array.from(document.querySelectorAll('button'));
    const saveBtn = btns.find(b => b.textContent.trim() === '\u4fdd\u5b58');
    if (saveBtn) {
      saveBtn.click();
      return 'clicked \u4fdd\u5b58';
    }
    return '\u4fdd\u5b58 not found';
  });
  console.log('Save result:', saveResult);
  await page.waitForTimeout(8000);

  console.log('Final URL:', page.url());
  await page.screenshot({ path: 'scf_after_save.png', fullPage: true });

  const finalText = await page.locator('body').innerText();
  console.log('Final text (first 1000):', finalText.substring(0, 1000));

  await browser.close();
})();
