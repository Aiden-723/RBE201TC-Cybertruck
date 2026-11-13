// Optional real-Chrome smoke test; uses only Node standard library and an installed browser.
const {spawn} = require('node:child_process');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const assert = require('node:assert/strict');
const pause = ms => new Promise(resolve => setTimeout(resolve, ms));
(async () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'console-browser-'));
  const out = process.env.EVIDENCE_DIR || dir;
  fs.mkdirSync(out, {recursive:true});
  const browser = spawn(process.env.CHROME || '/usr/bin/google-chrome', ['--headless','--no-sandbox','--disable-gpu','--no-first-run','--remote-debugging-port=0',`--user-data-dir=${dir}`], {stdio:'ignore'});
  let ws, authServer;
  try {
    const portFile = path.join(dir, 'DevToolsActivePort');
    for (let i=0; i<100 && !fs.existsSync(portFile); i++) await pause(100);
    const port = fs.readFileSync(portFile, 'utf8').split('\n')[0];
    const tab = await (await fetch(`http://127.0.0.1:${port}/json/new?about:blank`,{method:'PUT'})).json();
    ws = new WebSocket(tab.webSocketDebuggerUrl);
    await new Promise((resolve,reject)=>{ws.onopen=resolve;ws.onerror=reject;});
    let id=0; const pending = new Map(); const errors=[];
    ws.onmessage = event => {const m=JSON.parse(event.data); if(m.id){const p=pending.get(m.id);pending.delete(m.id);if(p) m.error?p.reject(Error(JSON.stringify(m.error))):p.resolve(m.result);} else if(m.method==='Runtime.exceptionThrown') errors.push(m.params.exceptionDetails.text);};
    const call = (method, params={})=>new Promise((resolve,reject)=>{pending.set(++id,{resolve,reject});ws.send(JSON.stringify({id,method,params}));});
    const evaluate = async expression => {const r=await call('Runtime.evaluate',{expression,returnByValue:true,awaitPromise:true});if(r.exceptionDetails)throw Error(JSON.stringify(r.exceptionDetails));return r.result.value;};
    const until = async expression => {for(let i=0;i<100;i++){if(await evaluate(expression))return;await pause(100);}throw Error(`Timeout: ${expression}`);};
    await call('Runtime.enable'); await call('Network.enable');
    await call('Emulation.setDeviceMetricsOverride',{width:1440,height:1200,deviceScaleFactor:1,mobile:false});
    await call('Page.navigate',{url:process.env.CONSOLE_URL || 'http://127.0.0.1:8765/'});
    await until("document.getElementById('connection')?.textContent === '设备已连接'");
    assert.equal(await evaluate('document.documentElement.scrollWidth <= innerWidth'),true);
    assert.equal(await evaluate("document.querySelectorAll('progress').length"),2);
    assert.ok(await evaluate("document.getElementById('host-kind').textContent.length > 0"));
    assert.match(await evaluate("document.getElementById('encoder-status').textContent"),/未验证/);
    assert.deepEqual(await evaluate("Array.from(document.querySelectorAll('#encoders strong'), n=>n.textContent)"),['—','—','—','—']);
    const screenshot = async filename => {
      const size = await evaluate('({width:innerWidth, height:document.documentElement.scrollHeight})');
      const image = await call('Page.captureScreenshot',{captureBeyondViewport:true,clip:{x:0,y:0,width:size.width,height:size.height,scale:1}});
      fs.writeFileSync(path.join(out, filename),Buffer.from(image.data,'base64'));
    };
    await screenshot('desktop-1440.png');
    await evaluate("document.getElementById('pause').click()");
    assert.equal(await evaluate("document.getElementById('connection').textContent"),'更新已暂停');
    await evaluate("document.getElementById('refresh').click()");
    await until("!document.getElementById('refresh').disabled");
    assert.equal(await evaluate("document.getElementById('pause').textContent"),'恢复更新');
    await evaluate("document.getElementById('settings').click()");
    assert.equal(await evaluate('document.activeElement.id'),'token');
    await call('Input.dispatchKeyEvent',{type:'keyDown',key:'Escape',code:'Escape'});
    assert.equal(await evaluate('document.activeElement.id'),'settings');
    await evaluate("document.getElementById('pause').click()");
    await until("document.getElementById('connection').textContent === '设备已连接'");
    await call('Emulation.setDeviceMetricsOverride',{width:390,height:844,deviceScaleFactor:1,mobile:true});
    await evaluate("document.querySelector('.technical').open=true; document.querySelector('.raw-detail').open=true");
    assert.equal(await evaluate('document.documentElement.scrollWidth <= innerWidth'),true);
    await evaluate("document.getElementById('settings').click()");
    assert.equal(await evaluate('document.documentElement.scrollWidth <= innerWidth'),true);
    await evaluate("document.getElementById('settings').click(); document.querySelector('.raw-detail').open=false; document.querySelector('.technical').open=false");
    await screenshot('mobile-390.png');
    await call('Network.emulateNetworkConditions',{offline:true,latency:0,downloadThroughput:-1,uploadThroughput:-1});
    await evaluate("document.getElementById('refresh').click()");
    await until("document.getElementById('connection').textContent === '连接中断'");
    assert.match(await evaluate("document.getElementById('snapshot-label').textContent"),/旧快照/);
    assert.equal(await evaluate("document.getElementById('connection').classList.contains('live')"),false);
    await screenshot('mobile-offline.png');
    await call('Network.emulateNetworkConditions',{offline:false,latency:0,downloadThroughput:-1,uploadThroughput:-1});
    await evaluate("document.getElementById('refresh').click()");
    await until("document.getElementById('connection').textContent === '设备已连接'");
    authServer = spawn('python3', ['server.py','--port','0'], {cwd:path.resolve(__dirname,'../../backend'),env:{...process.env,DEVICE_CONSOLE_TOKEN:'browser-test-fixture-not-a-user-secret'},stdio:['ignore','pipe','ignore']});
    const authPort = await new Promise((resolve,reject)=>{
      let text=''; const deadline=setTimeout(()=>reject(Error('auth server startup timed out')),5000);
      authServer.stdout.on('data', chunk=>{text+=chunk; if(text.includes('\n')){clearTimeout(deadline);resolve(JSON.parse(text.split('\n')[0]).port);}});
      authServer.once('error',reject);
    });
    await call('Page.navigate',{url:`http://127.0.0.1:${authPort}/`});
    await until("document.getElementById('connection')?.textContent === '需要令牌'");
    assert.match(await evaluate("document.getElementById('notice').textContent"),/连接设置/);
    await evaluate("document.getElementById('settings').click()");
    assert.equal(await evaluate('document.activeElement.id'),'token');
    assert.equal(await evaluate("document.getElementById('token').type"),'password');
    assert.equal(await evaluate('document.documentElement.scrollWidth <= innerWidth'),true);
    await screenshot('mobile-auth.png');
    assert.deepEqual(errors,[]);
    console.log('PASS: real Chrome; 1440px + 390px no overflow; actual host metrics; pause/manual refresh/resume; settings focus/Escape; raw JSON; offline stale retention; recovery; authenticated server public UI + actionable 401; 0 runtime exceptions');
    console.log(`Screenshots: ${out}`);
  } finally {
    if(authServer && authServer.exitCode === null) { authServer.kill('SIGTERM'); await new Promise(resolve=>authServer.once('exit',resolve)); }
    if(ws && ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify({id:999999,method:'Browser.close'}));
    await Promise.race([new Promise(resolve=>browser.once('exit',resolve)),pause(3000)]);
    if(ws)ws.close();
    if(browser.exitCode === null)browser.kill('SIGTERM');
    // Keep the isolated profile in TMPDIR for diagnostics; scratch retention cleans it.
    console.log(`Isolated browser profile: ${dir}`);
  }
})().catch(error=>{console.error(error);process.exitCode=1;});
