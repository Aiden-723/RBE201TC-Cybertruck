const {test}=require('node:test');
const assert=require('node:assert/strict');
const {WheelControl,DRIVE_KEYS}=require('../control.js');
const pause=ms=>new Promise(r=>setTimeout(r,ms));
function fixture(options={}) {
  const calls=[];
  const control=new WheelControl({interval:10,...options,fetch:async(url,req)=>{
    const data=req.body?JSON.parse(req.body):null;calls.push(data);
    return {ok:true,json:async()=>({data:{enabled:true,armed:data?.action==='arm'||data?.action==='drive'||data?.action==='release',
      state:data?.action==='drive'?'driving':'stopped',control_key:'test-key',...(data?.action==='arm'?{session:'test-session'}:{})}})};
  }});
  return {control,calls};
}
test('arrow and WASD controls use differential direction names',()=>{
  assert.equal(DRIVE_KEYS.ArrowLeft,'left');assert.equal(DRIVE_KEYS.ArrowRight,'right');
  assert.equal(DRIVE_KEYS.KeyW,'forward');assert.equal(DRIVE_KEYS.KeyS,'backward');
});
test('loading and arming never sends a drive packet',async()=>{
  const {control,calls}=fixture();await control.refresh();
  assert.deepEqual(calls,[null]);await control.arm();
  assert.equal(calls.at(-1).action,'arm');assert.equal(control.session,'test-session');
  assert.ok(!calls.some(x=>x?.action==='drive'));await control.stop();
});
test('held key sends heartbeats, release cancels them and orders stop after drive',async()=>{
  const {control,calls}=fixture();await control.arm(true);await control.start('forward',130);await pause(25);
  await control.release();const count=calls.length;await pause(30);
  assert.equal(calls.length,count);assert.equal(calls.at(-1).action,'release');
  const sequences=calls.filter(x=>x?.seq).map(x=>x.seq);
  assert.deepEqual(sequences,[...sequences].sort((a,b)=>a-b));await control.stop();
});
test('after stop the next fresh press reacquires control automatically',async()=>{
  const {control,calls}=fixture();await control.arm(true);await control.start('right',130);
  await control.stop('blur');const count=calls.length;await control.start('forward',130);await pause(30);
  assert.ok(calls.length>count);assert.equal(calls.at(-1).action,'drive');assert.equal(control.session,'test-session');await control.stop();
});
test('held input continues heartbeats without a duration timer',async()=>{
  const {control,calls}=fixture();await control.arm(true);await control.start('m2',130);
  await pause(80);assert.equal(calls.at(-1).action,'drive');assert.equal(control.session,'test-session');
  assert.equal(control.holdTimer,undefined);await control.release();await control.stop();
});
test('late motion responses cannot restore state after release',async()=>{
  const {control}=fixture();await control.arm(true);
  const original=control.fetch;let resolve;
  control.fetch=(url,req)=>JSON.parse(req.body)?.action==='drive'?new Promise(r=>resolve=r):original(url,req);
  const drive=control.start('forward',130);await pause(0);await control.release();
  resolve({ok:true,json:async()=>({data:{enabled:true,armed:true,state:'driving'}})});await drive;
  assert.notEqual(control.state.state,'driving');assert.equal(control.active,null);await control.stop();
});
test('first press acquires a session and moves without a separate enable step',async()=>{
  const {control,calls}=fixture();await control.refresh();await control.start('forward',130);
  assert.equal(calls.at(-2).action,'arm');assert.equal(calls.at(-1).action,'drive');await control.stop();
});
test('release during the initial handshake never sends a movement command',async()=>{
  const {control,calls}=fixture();await control.refresh();
  const original=control.fetch;let finish;
  control.fetch=(url,req)=>JSON.parse(req.body||'null')?.action==='arm'?new Promise(r=>finish=r):original(url,req);
  const start=control.start('forward',130);await pause(0);await control.release();
  finish({ok:true,json:async()=>({data:{enabled:true,armed:true,state:'armed',session:'late-session'}})});
  await start;assert.ok(!calls.some(x=>x?.action==='drive'));await control.stop();
});
test('focus loss during the initial handshake invalidates it without movement',async()=>{
  const {control,calls}=fixture();await control.refresh();
  const original=control.fetch;let finish;
  control.fetch=(url,req)=>JSON.parse(req.body||'null')?.action==='arm'?new Promise(r=>finish=r):original(url,req);
  const start=control.start('left',130);await pause(0);await control.stop('blur');
  finish({ok:true,json:async()=>({data:{enabled:true,armed:true,state:'armed',session:'late-session'}})});
  await start;assert.equal(control.session,null);assert.ok(!calls.some(x=>x?.action==='drive'));
});

test('combined directions cancel opposite axes and preserve duplicate key aliases',()=>{
  const {directionForKeys:direction}=require('../control.js');
  assert.equal(direction(new Set(['ArrowUp','ArrowRight'])),'forward_right');
  assert.equal(direction(new Set(['KeyW','KeyA'])),'forward_left');
  assert.equal(direction(new Set(['ArrowDown','KeyD'])),'backward_right');
  assert.equal(direction(new Set(['KeyS','ArrowLeft'])),'backward_left');
  assert.equal(direction(new Set(['ArrowUp','ArrowDown'])),null);
  assert.equal(direction(new Set(['ArrowLeft','ArrowRight'])),null);
  assert.equal(direction(new Set(['ArrowUp','KeyW'])),'forward');
  assert.equal(direction(new Set(['KeyW'])),'forward');
  assert.equal(direction(new Set(['KeyW','KeyS','KeyD'])),'right');
});
test('held straight input changes to arc then straight in the same session',async()=>{
  const {control,calls}=fixture();
  try {
    await control.start('forward',200);
    await control.start('forward_right',200);await pause(25);
    assert.equal(calls.at(-1).direction,'forward_right');
    await control.start('forward',200);await pause(25);
    assert.equal(calls.at(-1).direction,'forward');
    assert.equal(calls.filter(x=>x?.action==='arm').length,1);
    await control.release();const count=calls.length;await pause(25);
    assert.equal(calls.length,count);assert.equal(calls.at(-1).action,'release');
  } finally {await control.stop();}
});
test('second key during handshake uses latest combined input on first drive',async()=>{
  const {control,calls}=fixture();await control.refresh();
  const original=control.fetch;let finish;
  control.fetch=(url,req)=>JSON.parse(req.body||'null')?.action==='arm'?new Promise(r=>finish=r):original(url,req);
  const start=control.start('forward',200);await pause(0);
  await control.start('forward_right',200);
  finish({ok:true,json:async()=>({data:{enabled:true,armed:true,state:'armed',session:'combo-session'}})});
  try {await start;assert.equal(calls.at(-1).direction,'forward_right');}
  finally {await control.stop();}
});
test('direction changes do not create concurrent heartbeat requests',async()=>{
  const {control,calls}=fixture();await control.arm();
  const original=control.fetch;let finish;let drives=0;
  control.fetch=(url,req)=>{
    if(JSON.parse(req.body||'null')?.action==='drive'&&++drives===1)return new Promise(r=>finish=r);
    return original(url,req);
  };
  const first=control.start('forward',200);await pause(0);
  await control.start('forward_right',200);await control.start('forward_left',200);
  assert.equal(drives,1);
  finish({ok:true,json:async()=>({data:{enabled:true,armed:true,state:'driving'}})});
  try {await first;await pause(25);assert.equal(calls.at(-1).direction,'forward_left');}
  finally {await control.stop();}
});
