const { chromium } = require('C:/Users/Administrator/AppData/Roaming/npm/node_modules/playwright');

(async () => {
  const browser = await chromium.connectOverCDP('http://localhost:9222');
  const context = browser.contexts()[0];
  const page = context.pages()[0];

  console.log('URL:', page.url());
  await page.waitForTimeout(3000);

  // Get all visible text to understand form
  const bodyText = await page.locator('body').innerText();
  console.log('Body text (first 3000 chars):', bodyText.substring(0, 3000));

  // Get all select elements (dropdowns)
  const selectCount = await page.locator('select').count();
  console.log('Select count:', selectCount);

  // Get all input elements and their attributes
  const inputs = await page.locator('input').evaluateAll(inputs =>
    inputs.map(i => ({ type: i.type, placeholder: i.placeholder, name: i.name, id: i.id }))
  );
  console.log('Inputs:', JSON.stringify(inputs, null, 2));

  await page.screenshot({ path: 'scf_form_detail.png', fullPage: true });
  console.log('Screenshot saved');

  await browser.close();
})();
