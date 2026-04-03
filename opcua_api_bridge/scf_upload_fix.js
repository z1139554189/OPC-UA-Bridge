const { chromium } = require('C:/Users/Administrator/AppData/Roaming/npm/node_modules/playwright');

(async () => {
  const browser = await chromium.connectOverCDP('http://localhost:9222');
  const context = browser.contexts()[0];
  const page = context.pages()[0];

  await page.waitForTimeout(2000);

  // Step 1: Click the "本地上传zip包" text/label specifically
  const zipLabelResult = await page.evaluate(() => {
    const all = document.querySelectorAll('*');
    for (const el of all) {
      const text = el.textContent || '';
      if (text.includes('\u672c\u5730\u4e0a\u4f20zip\u5305') && el.tagName !== 'INPUT') {
        console.log('Found zip label:', el.tagName, el.className, el.textContent.substring(0, 50));
        el.click();
        return { tag: el.tagName, className: el.className };
      }
    }
    return null;
  });
  console.log('Zip label result:', zipLabelResult);
  await page.waitForTimeout(2000);

  // Step 2: Now find ALL file inputs (including hidden ones)
  const fileInputsInfo = await page.evaluate(() => {
    const inputs = Array.from(document.querySelectorAll('input[type="file"]'));
    return inputs.map(i => ({
      name: i.name,
      accept: i.accept,
      style: i.style.cssText,
      display: window.getComputedStyle(i).display,
      opacity: window.getComputedStyle(i).opacity,
      width: i.offsetWidth,
      height: i.offsetHeight
    }));
  });
  console.log('File inputs:', JSON.stringify(fileInputsInfo, null, 2));

  // Step 3: If no visible file input, try triggering the click via the input element
  if (fileInputsInfo.length === 0 || fileInputsInfo.every(fi => fi.display === 'none')) {
    // Find the input inside the label or nearby
    const triggerResult = await page.evaluate(() => {
      const labels = Array.from(document.querySelectorAll('label'));
      for (const label of labels) {
        if (label.textContent.includes('\u672c\u5730\u4e0a\u4f20zip\u5305')) {
          const input = label.querySelector('input[type="file"]');
          if (input) {
            console.log('Found file input in label');
            return 'found in label';
          }
          // Try clicking label which should trigger associated input
          label.click();
          return 'clicked label';
        }
      }
      // Try finding by tabindex or aria
      const inputs = Array.from(document.querySelectorAll('input'));
      for (const inp of inputs) {
        if (inp.style?.display === 'none' || inp.style?.visibility === 'hidden') {
          // Check if it's the file input
          const parent = inp.closest('div');
          if (parent && parent.textContent.includes('\u672c\u5730\u4e0a\u4f20')) {
            console.log('Hidden file input found, clicking via JS');
            inp.click();
            return 'clicked hidden file input';
          }
        }
      }
      return 'not found';
    });
    console.log('Trigger result:', triggerResult);
    await page.waitForTimeout(1000);
  }

  // Try setInputFiles on any file input
  const fileInput2 = page.locator('input[type="file"]');
  const count = await fileInput2.count();
  console.log('File input count after trigger:', count);

  if (count > 0) {
    // Try with the first one
    try {
      await fileInput2.first().setInputFiles('C:\\Users\\Administrator\\WorkBuddy\\20260326125244\\opcua_api_bridge\\cloud\\scf\\opcua_cloud_api.zip', { timeout: 5000 });
      console.log('ZIP set via setInputFiles');
    } catch (e) {
      console.log('setInputFiles failed:', e.message?.substring(0, 100));
      // Try evaluating to set files via dataTransfer
      await page.evaluate(() => {
        const dt = new DataTransfer();
        const file = new File([`
# -*- coding: utf-8 -*-
import os
import json
from datetime import datetime
from decimal import Decimal
from flask import Flask, request, jsonify
import pymysql
from pymysql.cursors import DictCursor

app = Flask(__name__)

def _get_conn():
    return pymysql.connect(
        host=os.environ.get("DB_HOST", "sh-cynosdbmysql-grp-4f512ckw.sql.tencentcdb.com"),
        port=int(os.environ.get("DB_PORT", "21397")),
        database=os.environ.get("DB_NAME", "opcua_db"),
        user=os.environ.get("DB_USER", "opcua_user"),
        password=os.environ.get("DB_PASSWORD", ""),
        charset="utf8mb4",
        cursorclass=DictCursor,
        connect_timeout=5,
    )

class MyEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, datetime):
            return obj.strftime("%Y-%m-%d %H:%M:%S")
        if isinstance(obj, Decimal):
            return round(float(obj), 2)
        return super().default(obj)

app.json_encoder = MyEncoder

@app.route("/")
def index():
    return jsonify({"status": "ok"})

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=9000)
`], 'index.py', { type: 'text/x-python' });

        const dt2 = new DataTransfer();
        dt2.items.add(file);

        const inputs = document.querySelectorAll('input[type="file"]');
        for (const inp of inputs) {
          if (inp.offsetWidth > 0 || inp.offsetHeight > 0) {
            Object.defineProperty(inp, 'files', { value: dt2.files, writable: true });
            inp.dispatchEvent(new Event('change', { bubbles: true }));
            console.log('File set via DataTransfer');
            return;
          }
        }
      });
    }
  }

  await page.waitForTimeout(2000);
  await page.screenshot({ path: 'scf_upload_fix.png', fullPage: true });

  // Check if 完成 is now enabled
  const doneBtn = page.locator('button').filter({ hasText: /\u5b8c\u6210|\u521b\u5efa/i }).first();
  const isDisabled = await doneBtn.isDisabled().catch(() => true);
  console.log('Done button disabled:', isDisabled);

  // Get function name value
  const funcName = await page.locator('input.app-scf-input').first().inputValue().catch(() => 'err');
  console.log('Function name:', funcName);

  await browser.close();
})();
