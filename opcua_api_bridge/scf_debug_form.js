const { chromium } = require('C:/Users/Administrator/AppData/Roaming/npm/node_modules/playwright');

(async () => {
  const browser = await chromium.connectOverCDP('http://localhost:9222');
  const context = browser.contexts()[0];
  const page = context.pages()[0];

  await page.waitForTimeout(2000);

  // Look for validation errors (red text)
  const errors = await page.evaluate(() => {
    const all = document.querySelectorAll('*');
    const errorMsgs = [];
    for (const el of all) {
      const style = window.getComputedStyle(el);
      const color = style.color;
      // Look for red-ish colors (common validation error color)
      if (color && (color.includes('255,') || color.includes('red'))) {
        const text = el.textContent?.trim();
        if (text && text.length > 3 && text.length < 200 && errorMsgs.length < 20) {
          errorMsgs.push({ text, color });
        }
      }
    }
    return errorMsgs;
  });
  console.log('Validation errors:', JSON.stringify(errors, null, 2));

  // Try JS click on 完成 button
  const doneResult = await page.evaluate(() => {
    const btns = Array.from(document.querySelectorAll('button'));
    const doneBtn = btns.find(b => b.textContent.trim() === '\u5b8c\u6210');
    if (doneBtn) {
      doneBtn.scrollIntoView();
      doneBtn.click();
      return 'clicked \u5b8c\u6210';
    }
    return '\u5b8c\u6210 not found';
  });
  console.log(doneResult);
  await page.waitForTimeout(5000);

  console.log('URL:', page.url());
  const afterText = await page.locator('body').innerText();
  console.log('After click (first 500):', afterText.substring(0, 500));

  await page.screenshot({ path: 'scf_debug_form.png', fullPage: true });
  await browser.close();
})();
