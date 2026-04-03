const { chromium } = require('C:/Users/Administrator/AppData/Roaming/npm/node_modules/playwright');

(async () => {
  const browser = await chromium.connectOverCDP('http://localhost:9222');
  const context = browser.contexts()[0];
  const page = context.pages()[0];

  await page.waitForTimeout(2000);

  // Check if textarea is visible and filled
  const textareaInfo = await page.evaluate(() => {
    const textareas = Array.from(document.querySelectorAll('textarea'));
    return textareas.map(t => ({ value: t.value.substring(0, 50), visible: t.offsetHeight > 0 }));
  });
  console.log('Textareas:', JSON.stringify(textareaInfo));

  // Check VPC state
  const vpcInfo = await page.evaluate(() => {
    const cbs = Array.from(document.querySelectorAll('input[type="checkbox"]'));
    return cbs.map(cb => ({ checked: cb.checked, visible: cb.offsetHeight > 0 }));
  });
  console.log('Checkboxes:', JSON.stringify(vpcInfo));

  // Check what the form shows around env vars and VPC
  const body = await page.locator('body').innerText();
  const envIdx = body.indexOf('DB_HOST');
  const vpcIdx = body.indexOf('VPC');
  console.log('DB_HOST in body:', envIdx > 0);
  console.log('VPC in body:', vpcIdx > 0, 'at', vpcIdx);

  await page.screenshot({ path: 'scf_check.png', fullPage: true });

  // Find 完 button and click it
  const clickDone = await page.evaluate(() => {
    const btns = Array.from(document.querySelectorAll('button'));
    const doneBtn = btns.find(b => b.textContent.trim() === '\u5b8c\u6210');
    if (doneBtn) {
      doneBtn.click();
      return 'clicked \u5b8c\u6210';
    }
    return '\u5b8c\u6210 not found';
  });
  console.log(clickDone);
  await page.waitForTimeout(5000);

  // Check URL after clicking
  console.log('URL after create:', page.url());
  await page.screenshot({ path: 'scf_after_create.png', fullPage: true });

  const afterText = await page.locator('body').innerText();
  console.log('After create (first 1000):', afterText.substring(0, 1000));

  await browser.close();
})();
