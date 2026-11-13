'use strict';
// Read-only live GUI verification. Never generate keyboard/pointer drive actions.
const {chromium} = require('/Users/your-user/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const base = 'http://127.0.0.1:8765';
const out = '/Users/your-user/Documents/Codex/2026-09-18/di/outputs/RBE201TC_RESUME_20261104';
const state = async () => {
  const response = await fetch(base + '/api/v1/control');
  assert.equal(response.status, 200);
  const result = (await response.json()).data;
  delete result.control_key;
  return result;
};
(async () => {
  fs.mkdirSync(out, {recursive:true});
  const before = await state();
  assert.equal(before.command_mode, 'speed');
  assert.equal(before.max_hold_seconds, null);
  assert.deepEqual(before.outputs, [0,0,0,0]);
  assert.equal(before.armed, false);
  const browser = await chromium.launch({executablePath:'/Users/your-user/Documents/Codex/2026-09-18/di/work/browser-cache/chromium_headless_shell-1234/chrome-headless-shell-mac-arm64/chrome-headless-shell'});
  const errors = [], blocked = [];
  try {
    const page = await browser.newPage({viewport:{width:1440,height:1000}});
    page.on('pageerror', error => errors.push(error.message));
    await page.route('**/api/v1/control', async route => {
      const request = route.request();
      if (request.method() !== 'GET') {
        // Even an accidental drive from page code must never reach real hardware.
        let body;
        try { body = request.postDataJSON(); } catch {}
        if (body?.action !== 'stop') {
          blocked.push(request.method() + ':' + (body?.action || 'unknown'));
          await route.abort();
          return;
        }
      }
      await route.continue();
    });
    await page.goto(base);
    await page.locator('#drive-status').filter({hasText:'键盘控制就绪'}).waitFor({timeout:15000});
    await page.locator('#camera-image').waitFor({state:'visible',timeout:15000});
    assert.equal(await page.locator('#drive-arm').count(), 0);
    assert.equal(await page.locator('#wheels-raised').count(), 0);
    assert.equal(await page.locator('#drive-speed').inputValue(), '200');
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
    await page.evaluate(() => {
      window.__readonlyLoads = {start:performance.now(),loaded:0};
      document.getElementById('camera-image').addEventListener('load', () => window.__readonlyLoads.loaded++);
    });
    // This bounded window measures image load events; no controls are pressed.
    await page.waitForTimeout(10000);
    const measured = await page.evaluate(() => ({
      loaded:window.__readonlyLoads.loaded,
      seconds:(performance.now()-window.__readonlyLoads.start)/1000,
      display:document.getElementById('camera-rate').textContent,
      connection:document.getElementById('connection').textContent,
      control:document.getElementById('drive-status').textContent
    }));
    await page.screenshot({path:path.join(out,'live-desktop.png'),fullPage:true});
    await page.setViewportSize({width:390,height:844});
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
    await page.screenshot({path:path.join(out,'live-mobile.png'),fullPage:true});
    const after = await state();
    assert.deepEqual(after.outputs,[0,0,0,0]);
    assert.equal(after.drive_commands_sent,before.drive_commands_sent);
    assert.deepEqual(errors,[]);
    assert.deepEqual(blocked,[]);
    const report = {verified_at:new Date().toISOString(),scope:'Live sensors and display only; no drive actions',camera:measured,control_before:before,control_after:after,errors,blocked};
    fs.writeFileSync(path.join(out,'live-readonly-check.json'),JSON.stringify(report,null,2)+'\n');
    console.log(JSON.stringify(report,null,2));
  } finally { await browser.close(); }
})().catch(error => {console.error(error);process.exitCode=1;});
