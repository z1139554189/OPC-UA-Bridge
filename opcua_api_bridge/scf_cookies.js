const { chromium } = require('C:/Users/Administrator/AppData/Roaming/npm/node_modules/playwright');

(async () => {
  const browser = await chromium.connectOverCDP('http://localhost:9222');
  const context = browser.contexts()[0];
  const page = context.pages()[0];

  // Get cookies from the腾讯云 domain
  const cookies = await context.cookies('https://console.cloud.tencent.com');
  console.log('Cookies count:', cookies.length);
  console.log('Cookie names:', cookies.map(c => c.name));

  // Check for session-related cookies
  const sessionCookie = cookies.find(c =>
    c.name.includes('session') ||
    c.name.includes('token') ||
    c.name.includes('auth') ||
    c.name.includes('login')
  );
  console.log('Session cookie:', sessionCookie?.name, sessionCookie?.value?.substring(0, 50));

  await browser.close();
})();
