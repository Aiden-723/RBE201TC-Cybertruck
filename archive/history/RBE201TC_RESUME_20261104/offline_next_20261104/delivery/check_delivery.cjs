'use strict';
// Local file viewer check only; no HTTP server, robot, or DSH GUI access.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {pathToFileURL} = require('node:url');
const {createHash} = require('node:crypto');
const {chromium} = require('/Users/your-user/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const executablePath = '/Users/your-user/Documents/Codex/2026-09-18/di/work/browser-cache/chromium_headless_shell-1234/chrome-headless-shell-mac-arm64/chrome-headless-shell';
(async()=>{
  const browser = await chromium.launch({executablePath, headless:true});
  const report = {recorded_at:new Date().toISOString(),real_chromium:true,network_requests:[],page_errors:[],passed:false};
  try {
    const context = await browser.newContext({viewport:{width:1280,height:900}});
    await context.route(/^https?:/,route=>{report.network_requests.push(route.request().url());return route.abort();});
    const page = await context.newPage();
    page.on('pageerror',error=>report.page_errors.push(error.message));
    const file = path.join(__dirname,'RBE201TC_OFFLINE_NEXT_20261104.html');
    await page.goto(pathToFileURL(file).href,{waitUntil:'load'});
    report.document = await page.evaluate(()=>{
      const ids = [...document.querySelectorAll('[id]')].map(e=>e.id);
      const links = [...document.querySelectorAll('a[href^="#"]')];
      return {title:document.title,script_count:document.scripts.length,
        duplicate_ids:ids.filter((id,i)=>ids.indexOf(id)!==i),
        missing_targets:links.map(e=>e.getAttribute('href').slice(1)).filter(id=>!document.getElementById(id)),
        internal_links:links.length,articles:document.querySelectorAll('article').length,
        source_details:document.querySelectorAll('#source-files > details').length,
        images:[...document.images].map(e=>({data_uri:e.src.startsWith('data:image/png;base64,'),loaded:e.complete&&e.naturalWidth>0})),
        has_unexecuted_matlab:document.body.textContent.includes('MATLAB实际执行0/32'),
        desktop_no_overflow:document.documentElement.scrollWidth<=innerWidth};
    });
    assert.equal(report.document.script_count,0);
    assert.deepEqual(report.document.duplicate_ids,[]);assert.deepEqual(report.document.missing_targets,[]);
    assert.equal(report.document.articles,8);assert.equal(report.document.source_details,146);
    assert(report.document.images.every(image=>image.data_uri&&image.loaded));
    assert(report.document.has_unexecuted_matlab);assert(report.document.desktop_no_overflow);
    await page.screenshot({path:path.join(__dirname,'viewer-desktop.png')});
    await page.setViewportSize({width:390,height:844});
    report.mobile_no_overflow = await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth);
    assert(report.mobile_no_overflow);
    await page.screenshot({path:path.join(__dirname,'viewer-mobile.png')});
    assert.deepEqual(report.page_errors,[]);assert.deepEqual(report.network_requests,[]);
    const digest = file=>createHash('sha256').update(fs.readFileSync(file)).digest('hex');
    report.html_sha256 = digest(file);
    report.zip_sha256 = digest(path.join(__dirname,'RBE201TC_OFFLINE_NEXT_20261104.zip'));
    report.browser_version = browser.version();report.node_version = process.version;report.passed=true;
  } finally {
    await browser.close();
    fs.writeFileSync(path.join(__dirname,'OPENABLE_DELIVERY_CHECK.json'),JSON.stringify(report,null,2));
    console.log(JSON.stringify(report,null,2));
  }
})().catch(error=>{console.error(error);process.exitCode=1;});
