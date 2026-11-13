// Exercise only the FakeSerial preview server at 8878. Never point this at hardware.
const {chromium}=require('/Users/your-user/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const assert=require('node:assert/strict');
const base='http://127.0.0.1:8878';
const out='/Users/your-user/Documents/Codex/2026-09-18/di/work/studio-qa';
(async()=>{
 const browser=await chromium.launch({executablePath:'/Users/your-user/Documents/Codex/2026-09-18/di/work/browser-cache/chromium_headless_shell-1234/chrome-headless-shell-mac-arm64/chrome-headless-shell'});
 try {
  const page=await browser.newPage({viewport:{width:1440,height:1000}}),errors=[];
  page.on('pageerror',e=>errors.push(e.message));
  const state=async()=> (await (await fetch(base+'/api/v1/control')).json()).data;
  const until=async predicate=>{for(let i=0;i<100;i++){if(await predicate())return;await page.waitForTimeout(30);}throw Error('state timeout');};
  await page.goto(base);await until(async()=>await page.locator('#drive-status').innerText()==='键盘控制就绪');
  assert.equal(await page.locator('#drive-arm').count(),0);
  assert.equal(await page.locator('#wheels-raised').count(),0);
  assert.deepEqual((await state()).outputs,[0,0,0,0]);
  assert.equal((await state()).command_mode,'speed');
  const arm=async()=>{await until(async()=>await page.locator('[data-drive=forward]').isEnabled());};
  for(const [key,outputs] of [['ArrowUp',[200,200,200,200]],['ArrowDown',[-200,-200,-200,-200]],['ArrowLeft',[-200,-200,200,200]],['ArrowRight',[200,200,-200,-200]]]){
   await page.keyboard.down(key);await until(async()=>JSON.stringify((await state()).outputs)===JSON.stringify(outputs));
   await page.waitForTimeout(180);await page.keyboard.up(key);
   await until(async()=> (await state()).outputs.every(x=>x===0));
  }
  await page.locator('[data-drive=m3]').hover();await page.mouse.down();
  await until(async()=>JSON.stringify((await state()).outputs)==='[0,0,200,0]');
  await page.mouse.up();await until(async()=> (await state()).outputs.every(x=>x===0));
  await page.keyboard.down('KeyW');await until(async()=> (await state()).state==='driving');
  await page.keyboard.press('Space');await until(async()=>!(await state()).armed);await page.keyboard.up('KeyW');
  await arm();await page.keyboard.down('ArrowUp');await until(async()=> (await state()).state==='driving');
  await page.evaluate(()=>window.dispatchEvent(new Event('blur')));
  await until(async()=>!(await state()).armed);await page.keyboard.up('ArrowUp');
  await arm();await page.keyboard.down('ArrowUp');await until(async()=> (await state()).state==='driving');
  await page.waitForTimeout(4200);assert.equal((await state()).armed,true);assert.deepEqual((await state()).outputs,[200,200,200,200]);
  await page.context().setOffline(true);await page.waitForTimeout(650);
  assert.equal((await state()).armed,false);assert.deepEqual((await state()).outputs,[0,0,0,0]);
  await page.keyboard.up('ArrowUp');await page.context().setOffline(false);
  await page.reload();await until(async()=>await page.locator('#drive-status').innerText()==='键盘控制就绪');
  await page.keyboard.down('ArrowUp');await until(async()=> (await state()).state==='driving');
  await page.keyboard.up('ArrowUp');await until(async()=> (await state()).outputs.every(x=>x===0));
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);
  await page.screenshot({path:out+'/wheel-desktop-simulated.png',fullPage:true});
  await page.setViewportSize({width:390,height:844});
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);
  await page.screenshot({path:out+'/wheel-mobile-simulated.png',fullPage:true});
  assert.deepEqual(errors,[]);console.log('PASS: real browser arrow/WASD, pointer single wheel, release, space, blur, continuous hold beyond 4 seconds, offline lease, reload, desktop/mobile. FakeSerial only.');
 } finally {await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
