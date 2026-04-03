const { chromium } = require('C:/Users/Administrator/AppData/Roaming/npm/node_modules/playwright');

(async () => {
  const browser = await chromium.connectOverCDP('http://localhost:9222');
  const context = browser.contexts()[0];
  const page = context.pages()[0];

  await page.waitForTimeout(2000);

  // Find all inputs and their validation states
  const inputStates = await page.evaluate(() => {
    const inputs = Array.from(document.querySelectorAll('input, textarea, select'));
    const results = [];
    for (const inp of inputs) {
      const rect = inp.getBoundingClientRect();
      if (rect.width > 0 && rect.height > 0) {
        const parent = inp.closest('div');
        results.push({
          type: inp.type,
          name: inp.name,
          placeholder: inp.placeholder?.substring(0, 50),
          value: inp.value?.substring(0, 30),
          disabled: inp.disabled,
          required: inp.required,
          visible: rect.width > 0 && rect.height > 0,
          parentText: parent?.textContent?.substring(0, 80)
        });
      }
    }
    return results;
  });
  console.log('Input states:', JSON.stringify(inputStates, null, 2));

  // Check done button properties
  const doneProps = await page.evaluate(() => {
    const btns = Array.from(document.querySelectorAll('button'));
    const doneBtn = btns.find(b => b.textContent.trim() === '\u5b8c\u6210');
    if (!doneBtn) return 'not found';
    return {
      text: doneBtn.textContent.trim(),
      disabled: doneBtn.disabled,
      className: doneBtn.className,
      style: doneBtn.style.cssText,
      attributes: {
        'data-disable': doneBtn.getAttribute('data-disable'),
        'aria-disabled': doneBtn.getAttribute('aria-disabled')
      }
    };
  });
  console.log('Done button props:', JSON.stringify(doneProps));

  // Check for any error messages or warnings
  const errorTexts = await page.evaluate(() => {
    const all = document.querySelectorAll('*');
    const errors = [];
    for (const el of all) {
      const style = window.getComputedStyle(el);
      const color = style.color;
      const bgColor = style.backgroundColor;
      // Look for red/orange text
      if ((color === 'rgb(224, 64, 46)' || color === 'rgb(242, 86, 68)') && el.textContent.trim().length > 2) {
        errors.push({ text: el.textContent.trim().substring(0, 100), color });
      }
    }
    return errors;
  });
  console.log('Error texts:', JSON.stringify(errorTexts));

  // Check ZIP upload status
  const zipStatus = await page.evaluate(() => {
    const allText = document.body.innerText;
    const hasZipIndicator = allText.includes('.zip') || allText.includes('\u6587\u4ef6\u540d') || allText.includes('opcua');
    const fileInputs = Array.from(document.querySelectorAll('input[type="file"]'));
    return {
      hasZipIndicator,
      fileInputsCount: fileInputs.length,
      bodyLength: allText.length
    };
  });
  console.log('ZIP status:', JSON.stringify(zipStatus));

  await page.screenshot({ path: 'scf_validate.png', fullPage: true });
  await browser.close();
})();
