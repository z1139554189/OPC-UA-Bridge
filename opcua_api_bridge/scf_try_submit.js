const { chromium } = require('C:/Users/Administrator/AppData/Roaming/npm/node_modules/playwright');

(async () => {
  const browser = await chromium.connectOverCDP('http://localhost:9222');
  const context = browser.contexts()[0];
  const page = context.pages()[0];

  await page.waitForTimeout(2000);

  // Try to directly call the form submit or submit button click with waiting
  // First, let's check if the page has any blocking modals or loading states
  const isLoading = await page.evaluate(() => {
    const loaders = document.querySelectorAll('.loading, .spinner, [class*="loading"], [class*="spinner"]');
    return loaders.length;
  });
  console.log('Loading indicators:', isLoading);

  // Try clicking 完成 with waitForNavigation
  try {
    await Promise.all([
      page.waitForNavigation({ timeout: 10000 }).catch(() => {}),
      page.evaluate(() => {
        const btns = Array.from(document.querySelectorAll('button'));
        const doneBtn = btns.find(b => b.textContent.trim() === '\u5b8c\u6210');
        if (doneBtn) {
          doneBtn.click();
          console.log('Clicked \u5b8c\u6210');
        }
      }),
      page.waitForTimeout(8000)
    ]);
  } catch (e) {
    console.log('Navigation wait error:', e.message?.substring(0, 100));
  }

  console.log('URL after submit:', page.url());
  await page.screenshot({ path: 'scf_try_submit.png', fullPage: true });

  const text = await page.locator('body').innerText();
  // Check if we're now on function list page (creation succeeded)
  const isFunctionList = text.includes('\u51fd\u6570\u540d\u79f0') && !text.includes('\u5b8c\u6210');
  console.log('Creation succeeded (function list visible):', isFunctionList);
  console.log('Function name visible:', text.includes('opcua-cloud-api'));

  await browser.close();
})();
