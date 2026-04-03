const { chromium } = require('C:/Users/Administrator/AppData/Roaming/npm/node_modules/playwright');

(async () => {
  const browser = await chromium.connectOverCDP('http://localhost:9222');
  const context = browser.contexts()[0];
  const page = context.pages()[0];

  // Debug: find all clickable elements with "从头开始"
  const allWithText = await page.locator('*').filter({ hasText: '^从头开始$' }).evaluateAll(els =>
    els.map(el => ({ tag: el.tagName, class: el.className, id: el.id, role: el.getAttribute('role'), text: el.innerText?.substring(0, 50) }))
  );
  console.log('Elements matching "^从头开始$":', JSON.stringify(allWithText, null, 2));

  // Try clicking the tab/panel
  const tabPanel = page.locator('[role="tabpanel"], [role="tab"]').filter({ hasText: /从头开始/ });
  console.log('Tab/panel count:', await tabPanel.count());

  // Try divs with specific classes
  const divs = await page.locator('div').filter({ hasText: /^从头开始$/ }).all();
  console.log('Matching divs:', divs.length);
  if (divs.length > 0) {
    const box = await divs[0].boundingBox();
    console.log('First match bounding box:', box);
  }

  await browser.close();
})();
