const { chromium } = require('C:/Users/Administrator/AppData/Roaming/npm/node_modules/playwright');

(async () => {
  const browser = await chromium.connectOverCDP('http://localhost:9222');
  const context = browser.contexts()[0];
  const page = context.pages()[0];

  await page.waitForTimeout(2000);

  // Use JS to click and interact
  // 1. Click "导入" button (env vars)
  const clickResult1 = await page.evaluate(() => {
    const btns = Array.from(document.querySelectorAll('button'));
    const btn = btns.find(b => b.textContent.trim() === '\u5bfc\u5165');
    if (btn) {
      btn.click();
      return 'clicked \u5bfc\u5165';
    }
    return 'not found';
  });
  console.log(clickResult1);
  await page.waitForTimeout(1000);

  // 2. Fill textarea for env vars
  const fillResult = await page.evaluate(() => {
    const textareas = Array.from(document.querySelectorAll('textarea'));
    if (textareas.length > 0) {
      textareas[0].value = 'DB_HOST=sh-cynosdbmysql-grp-4f512ckw.sql.tencentcdb.com\nDB_PORT=21397\nDB_NAME=opcua_db\nDB_USER=opcua_user\nDB_PASSWORD=Admin_00';
      textareas[0].dispatchEvent(new Event('input', { bubbles: true }));
      return 'filled textarea';
    }
    return 'no textarea';
  });
  console.log(fillResult);
  await page.waitForTimeout(500);

  // 3. Scroll down to VPC section and enable it
  await page.evaluate(() => {
    const sections = Array.from(document.querySelectorAll('div'));
    for (const div of sections) {
      if (div.textContent.includes('\u5f00\u542f\u56fa\u5b9a\u5185\u7f51\u51fa\u53e3 IP')) {
        div.scrollIntoView();
        return;
      }
    }
  });
  await page.waitForTimeout(500);

  // 4. Enable VPC by finding the toggle
  const vpcResult = await page.evaluate(() => {
    const all = Array.from(document.querySelectorAll('*'));
    for (const el of all) {
      if (el.textContent.includes('\u5f00\u542f\u56fa\u5b9a\u5185\u7f51\u51fa\u53e3 IP')) {
        console.log('Found VPC toggle parent:', el.tagName, el.className);
        // Look for checkbox or switch
        const parent = el.closest ? el.closest('div') : el.parentElement;
        if (parent) {
          const checkbox = parent.querySelector('input[type="checkbox"]');
          if (checkbox && !checkbox.checked) {
            checkbox.click();
            return 'VPC checkbox clicked';
          }
        }
      }
    }
    return 'VPC toggle not found';
  });
  console.log(vpcResult);
  await page.waitForTimeout(500);

  // 5. Scroll to bottom and find 创建 button
  await page.evaluate(() => window.scrollTo(0, document.body.scrollHeight));
  await page.waitForTimeout(1000);

  const createResult = await page.evaluate(() => {
    const btns = Array.from(document.querySelectorAll('button'));
    const createBtn = btns.find(b =>
      b.textContent.includes('\u5b8c\u6210') ||
      b.textContent.includes('\u521b\u5efa') ||
      b.textContent.includes('\u63d0\u4ea4')
    );
    if (createBtn) {
      console.log('Found create button:', createBtn.textContent, createBtn.className);
      return createBtn.textContent.trim();
    }
    return 'not found';
  });
  console.log('Create button text:', createResult);

  // Screenshot
  await page.screenshot({ path: 'scf_config2.png', fullPage: true });

  // Get current state
  const text = await page.locator('body').innerText();
  console.log('State (env var section):', text.includes('sh-cynosdbmysql') ? 'ENV VARS FILLED' : 'env vars NOT filled');
  console.log('State (VPC):', vpcResult);

  await browser.close();
})();
