const { chromium } = require('C:/Users/Administrator/AppData/Roaming/npm/node_modules/playwright');

(async () => {
  const browser = await chromium.connectOverCDP('http://localhost:9222');
  const context = browser.contexts()[0];
  const page = context.pages()[0];

  await page.waitForTimeout(2000);

  // === Step 1: Add Environment Variables ===
  // Click "导入" button for env vars
  const importBtn = page.locator('button').filter({ hasText: '导入' }).first();
  if (await importBtn.count() > 0) {
    await importBtn.click();
    console.log('Clicked 导入');
    await page.waitForTimeout(1000);

    // Look for textarea or input for key-value pairs
    const textarea = page.locator('textarea').first();
    if (await textarea.count() > 0) {
      await textarea.fill('DB_HOST=sh-cynosdbmysql-grp-4f512ckw.sql.tencentcdb.com\nDB_PORT=21397\nDB_NAME=opcua_db\nDB_USER=opcua_user\nDB_PASSWORD=Admin_00');
      console.log('Filled env vars');
      await page.waitForTimeout(500);
    }
  } else {
    console.log('Import button not found, looking for add button');
    // Try clicking "+" or "添加" to add individual env vars
    const addBtns = await page.locator('button').filter({ hasText: /\u6dfb\u52a0|\+/ }).all();
    console.log('Add buttons:', addBtns.length);
  }

  await page.screenshot({ path: 'scf_env.png', fullPage: true });

  // === Step 2: Enable VPC ===
  // Find VPC section and check the checkbox/ toggle
  // Look for "VPC私有网络" text and find nearby toggle/checkbox
  const vpcToggle = page.locator('div').filter({ hasText: /VPC\u79c1\u6709\u7f51\u7edc/ }).locator('..').locator('input[type="checkbox"], .ivu-switch, div[role="switch"]').first();
  console.log('VPC toggle count:', await vpcToggle.count());

  // Try clicking on the VPC section itself
  await page.evaluate(() => {
    const all = document.querySelectorAll('*');
    for (const el of all) {
      if (el.textContent.includes('\u5f00\u542f\u56fa\u5b9a\u5185\u7f51\u51fa\u53e3 IP')) {
        console.log('Found VPC checkbox/label at', el.className, el.tagName);
        el.click();
        return;
      }
    }
  });
  await page.waitForTimeout(1000);
  await page.screenshot({ path: 'scf_vpc.png', fullPage: true });

  // === Step 3: Find and click 创建 button ===
  const createBtn = page.locator('button').filter({ hasText: /\u5b8c\u6210|\u521b\u5efa|\u63d0\u4ea4/i }).first();
  if (await createBtn.count() > 0) {
    console.log('Found create button, text:', await createBtn.innerText());
  }

  const fullText = await page.locator('body').innerText();
  console.log('Form sections after config:', fullText.substring(fullText.indexOf('环境变量'), fullText.indexOf('环境变量') + 500));

  await browser.close();
})();
