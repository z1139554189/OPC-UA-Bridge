const { chromium } = require('C:/Users/Administrator/AppData/Roaming/npm/node_modules/playwright');

(async () => {
  const browser = await chromium.connectOverCDP('http://localhost:9222');
  const context = browser.contexts()[0];
  const page = context.pages()[0];

  await page.waitForTimeout(3000);

  // Use JS to find and interact with form elements
  const result = await page.evaluate(() => {
    const inputs = document.querySelectorAll('input');
    const results = [];
    inputs.forEach(i => {
      const rect = i.getBoundingClientRect();
      results.push({
        type: i.type,
        placeholder: i.placeholder,
        class: i.className,
        visible: rect.width > 0 && rect.height > 0,
        rect: { w: rect.width, h: rect.height, top: rect.top }
      });
    });
    return results;
  });

  console.log('All inputs:', JSON.stringify(result, null, 2));

  // Try clicking "Web函数" via JS
  await page.evaluate(() => {
    const all = document.querySelectorAll('div');
    for (const div of all) {
      if (div.textContent.trim() === 'Web函数') {
        div.click();
        console.log('Clicked Web函数 via JS');
        return;
      }
    }
  });

  await page.waitForTimeout(1000);

  // Now find the function name input
  const inputsAfter = await page.evaluate(() => {
    const inputs = document.querySelectorAll('input');
    const results = [];
    inputs.forEach(i => {
      const rect = i.getBoundingClientRect();
      results.push({
        type: i.type,
        placeholder: i.placeholder,
        class: i.className,
        visible: rect.width > 0 && rect.height > 0,
        rect: { w: Math.round(rect.width), h: Math.round(rect.height) }
      });
    });
    return results;
  });
  console.log('Inputs after clicking Web函数:', JSON.stringify(inputsAfter, null, 2));

  await page.screenshot({ path: 'scf_debug_inputs.png', fullPage: true });
  await browser.close();
})();
