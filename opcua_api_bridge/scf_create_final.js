const { chromium } = require('C:/Users/Administrator/AppData/Roaming/npm/node_modules/playwright');

(async () => {
  const browser = await chromium.connectOverCDP('http://localhost:9222');
  const context = browser.contexts()[0];
  const page = context.pages()[0];

  await page.waitForTimeout(2000);

  // We're on the create page. Function name and ZIP should already be set.
  // Step 1: Verify function name
  const funcInput = page.locator('input.app-scf-input').first();
  const funcName = await funcInput.inputValue().catch(() => 'not found');
  console.log('Current function name:', funcName);

  // Step 2: Scroll down to find and fill environment variables section
  // Also find VPC section
  const body = await page.locator('body').innerText();

  // Find "环境变量" section
  const hasEnvVar = body.includes('环境变量');
  console.log('Has env var section:', hasEnvVar);

  // Find "VPC" or "VPC网络"
  const hasVPC = body.includes('VPC') || body.includes('络');
  console.log('Has VPC section:', hasVPC);

  // Scroll to bottom of form
  await page.evaluate(() => window.scrollTo(0, document.body.scrollHeight));
  await page.waitForTimeout(1000);
  await page.screenshot({ path: 'scf_form_bottom.png', fullPage: true });

  // Check for "完成" or "创建" button
  const createBtn = page.locator('button').filter({ hasText: /完成|创建|提交/i }).first();
  const createBtnCount = await page.locator('button').filter({ hasText: /完成|创建|提交/i }).count();
  console.log('Create button count:', createBtnCount);

  // Get the full form text again to understand what's remaining
  const fullText = await page.locator('body').innerText();
  const envIdx = fullText.indexOf('环境变量');
  const vpcIdx = fullText.indexOf('VPC');
  const triggerIdx = fullText.indexOf('触发器配置');

  console.log('Form sections - env:', envIdx > 0, 'vpc:', vpcIdx > 0, 'trigger:', triggerIdx > 0);
  console.log('Section texts:');
  if (envIdx > 0) console.log('ENV:', fullText.substring(envIdx, envIdx + 300));
  if (vpcIdx > 0) console.log('VPC:', fullText.substring(vpcIdx, vpcIdx + 300));

  await browser.close();
})();
