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

test('mode switch cancels held control and blocks new manual requests',async()=>{
 const {control,calls}=fixture();await control.refresh();await control.start('forward',200);
 const original=control.fetch;let complete;
 control.fetch=(url,req)=>JSON.parse(req.body||'null')?.action==='set_mode'?new Promise(r=>complete=r):original(url,req);
 const switching=control.setMode('automatic');await pause(0);const count=calls.length;
 await control.start('right',200);await pause(25);assert.equal(calls.length,count);assert.equal(control.session,null);assert.equal(control.active,null);
 complete({ok:true,json:async()=>({data:{enabled:true,armed:false,state:'stopped',outputs:[0,0,0,0],driving_mode:'automatic'}})});
 await switching;await control.start('forward',200);assert.equal(calls.length,count);
});
test('failed mode request leaves controls offline without a session',async()=>{
 const {control}=fixture();await control.refresh();control.fetch=async()=>{throw Error('network');};
 await control.setMode('automatic');assert.equal(control.state.state,'offline');assert.equal(control.session,null);assert.equal(control.active,null);
});
test('late drive response cannot undo automatic mode',async()=>{
 const {control}=fixture();await control.refresh();let reply;const original=control.fetch;
 control.fetch=(url,req)=>{const action=JSON.parse(req.body||'null')?.action;
  if(action==='drive')return new Promise(r=>reply=r);
  if(action==='set_mode')return Promise.resolve({ok:true,json:async()=>({data:{enabled:true,armed:false,state:'stopped',outputs:[0,0,0,0],driving_mode:'automatic'}})});
  return original(url,req);};
 const drive=control.start('forward',200);await pause(0);await control.setMode('automatic');
 reply({ok:true,json:async()=>({data:{enabled:true,armed:true,state:'driving',driving_mode:'manual'}})});await drive;
 assert.equal(control.state.driving_mode,'automatic');assert.equal(control.state.state,'stopped');assert.equal(control.active,null);
});

// All requests below are in-memory: no real server, port 8765, GPIO, or serial devices.
const reply=data=>({ok:true,json:async()=>({schema_version:'1.0',data})});
const deferred=()=>{let resolve,reject;const promise=new Promise((yes,no)=>{resolve=yes;reject=no;});return {promise,resolve,reject};};
const flush=()=>new Promise(resolve=>setImmediate(resolve));
function autoFixture({mode='automatic',interval=8}={}) {
  const calls=[],requests=[];let handler;
  const server={enabled:true,armed:false,state:'stopped',driving_mode:mode,control_key:'test-key',outputs:[0,0,0,0],
    autonomy:{ready:true,running:false,state:'ready',reason:null,last_values:[1,0,0,1]}};
  const snapshot=()=>structuredClone(server);
  const armed=()=>({...snapshot(),armed:true,state:'armed',session:'motor-session-is-not-auto',autonomy_session:'opaque-auto-owner',
    autonomy:{...server.autonomy,running:true,state:'waiting_heartbeat'}});
  const fetch=async(url,request)=>{
    assert.equal(url,'/api/v1/control');
    const body=request.body?JSON.parse(request.body):null;calls.push(body);requests.push({url,request});
    if(handler){const result=handler(body,request);if(result!==undefined)return result;}
    if(body?.action==='autonomy_start')Object.assign(server,armed());
    if(body?.action==='autonomy_heartbeat'){
      assert.equal(body.autonomy_session,'opaque-auto-owner');
      server.armed=true;server.state='driving';server.outputs=[140,140,140,140];
      server.autonomy={...server.autonomy,running:true,state:'straight'};
    }
    if(body?.action==='stop'){
      server.armed=false;server.state='stopped';server.outputs=[0,0,0,0];
      server.autonomy={...server.autonomy,running:false,state:'stopped',reason:null};
    }
    if(body?.action==='set_mode')server.driving_mode=body.mode;
    const state=snapshot();
    // Only the successful start response carries ownership, never GET/heartbeat/STOP.
    if(body?.action!=='autonomy_start')delete state.autonomy_session;
    return reply(state);
  };
  return {control:new WheelControl({fetch,autonomyInterval:interval}),fetch,calls,requests,server,snapshot,armed,
    handle:fn=>{handler=fn;}};
}

test('automatic mode selection and polling only observe; manual default remains 200',async()=>{
  const f=autoFixture({mode:'manual'});await f.control.refresh();await f.control.setMode('automatic');
  await f.control.refresh();await pause(20);
  assert.deepEqual(f.calls.filter(Boolean),[{action:'stop'},{action:'set_mode',mode:'automatic'}]);
  assert.equal(f.control.autoSession,null);assert.equal(f.control.canStartAutonomy(),true);
  const manual=fixture();await manual.control.start('left');
  assert.equal(manual.calls.at(-1).speed,200);await manual.control.stop();
});

test('explicit start waits for its opaque token before heartbeat 1 and preserves request security',async()=>{
  const f=autoFixture();const pending=deferred();
  f.handle(body=>body?.action==='autonomy_start'?pending.promise:undefined);
  f.control.token='access-token';await f.control.refresh();
  const start=f.control.startAutonomy();await flush();
  assert.deepEqual(f.calls.filter(Boolean),[{action:'autonomy_start',speed:50}]);
  assert.equal(f.control.autoSession,null);assert.deepEqual(f.control.state.outputs,[0,0,0,0]);
  pending.resolve(reply(f.armed()));
  try {
    await start;
    const beats=f.calls.filter(body=>body?.action==='autonomy_heartbeat');
    assert.deepEqual(beats,[{action:'autonomy_heartbeat',autonomy_session:'opaque-auto-owner',seq:1}]);
    assert.equal(f.control.session,null);assert.equal(f.control.autoSession,'opaque-auto-owner');
    assert.equal(f.control.state.autonomy_session,undefined);
    assert.equal(new WheelControl({fetch:f.fetch}).autonomyInterval,200);
    for(const {request} of f.requests){
      assert.equal(request.headers.Authorization,'Bearer access-token');
      assert.equal(request.credentials,'omit');assert.equal(request.cache,'no-store');assert.equal(request.redirect,'error');
      if(request.method==='POST'){
        assert.equal(request.headers['X-Control-Key'],'test-key');assert.equal(request.headers['Content-Type'],'application/json');
      }
    }
  } finally {await f.control.stop();}
});

test('automatic heartbeats increase seq without overlapping a pending request or duplicate start',async()=>{
  const f=autoFixture();await f.control.refresh();const pending=deferred();let beats=0;
  f.handle(body=>body?.action==='autonomy_heartbeat'&&++beats===1?pending.promise:undefined);
  const start=f.control.startAutonomy();await flush();
  await f.control.startAutonomy();await pause(30);
  assert.equal(beats,1);assert.equal(f.calls.filter(body=>body?.action==='autonomy_start').length,1);
  pending.resolve(reply({...f.snapshot(),armed:true,autonomy:{ready:true,running:true,state:'straight'}}));
  try {
    await start;await pause(30);
    const sequences=f.calls.filter(body=>body?.action==='autonomy_heartbeat').map(body=>body.seq);
    assert.ok(sequences.length>=2);assert.deepEqual(sequences,sequences.map((_,i)=>i+1));
  } finally {await f.control.stop();}
});

test('STOP during pending start rejects the late token and sends no movement heartbeat',async()=>{
  const f=autoFixture();await f.control.refresh();const pending=deferred();
  f.handle(body=>body?.action==='autonomy_start'?pending.promise:undefined);
  const start=f.control.startAutonomy();await flush();await f.control.stop();
  pending.resolve(reply(f.armed()));await start;await pause(20);
  assert.equal(f.control.autoSession,null);assert.equal(f.control.autoTimer,null);assert.equal(f.control.autoSeq,0);
  assert.equal(f.control.session,null);assert.equal(f.control.active,null);
  assert.ok(!f.calls.some(body=>body?.action==='autonomy_heartbeat'));
  assert.equal(f.calls.at(-1).action,'stop');assert.equal(f.control.state.state,'stopped');
});

test('mode switch during pending start rejects its late response and never resumes auto',async()=>{
  const f=autoFixture();await f.control.refresh();const pending=deferred();
  f.handle(body=>body?.action==='autonomy_start'?pending.promise:undefined);
  const start=f.control.startAutonomy();await flush();await f.control.setMode('manual');
  pending.resolve(reply(f.armed()));await start;await pause(20);
  assert.equal(f.control.state.driving_mode,'manual');assert.equal(f.control.autoSession,null);
  assert.ok(!f.calls.some(body=>body?.action==='autonomy_heartbeat'));
});

test('STOP and late in-flight heartbeat cannot restore motion or schedule another heartbeat',async()=>{
  const f=autoFixture();await f.control.refresh();const pending=deferred();
  f.handle(body=>body?.action==='autonomy_heartbeat'?pending.promise:undefined);
  const start=f.control.startAutonomy();await flush();
  await f.control.stop();const count=f.calls.length;
  pending.resolve(reply({...f.armed(),state:'driving',outputs:[140,140,140,140]}));await start;await pause(25);
  assert.equal(f.calls.length,count);assert.equal(f.control.state.state,'stopped');
  assert.deepEqual(f.control.state.outputs,[0,0,0,0]);assert.equal(f.control.autoSession,null);
});

test('network loss actively attempts STOP, clears ownership, and does not invent zero outputs',async()=>{
  const f=autoFixture();await f.control.refresh();await f.control.startAutonomy();
  f.handle(()=>{throw Error('network disconnected');});
  await pause(25);
  assert.equal(f.calls.at(-1).action,'stop');assert.equal(f.control.state.state,'offline');
  assert.deepEqual(f.control.state.outputs,[140,140,140,140]);
  assert.equal(f.control.state.reason,'connection_lost');assert.equal(f.control.autoSession,null);
  assert.equal(f.control.session,null);assert.equal(f.control.autoTimer,null);const count=f.calls.length;
  await pause(25);assert.equal(f.calls.length,count);
});

test('lost start response attempts STOP without any automatic heartbeat or retry',async()=>{
  const f=autoFixture();await f.control.refresh();
  f.handle(body=>{if(body?.action==='autonomy_start')throw Error('lost start response');});
  await f.control.startAutonomy();await pause(20);
  assert.deepEqual(f.calls.filter(Boolean),[{action:'autonomy_start',speed:50},{action:'stop'}]);
  assert.equal(f.control.autoSession,null);assert.equal(f.control.autoTimer,null);
});

test('missing autonomy token never falls back to the motor session or a GET token',async()=>{
  const f=autoFixture();await f.control.refresh();
  f.handle(body=>{
    if(body?.action==='autonomy_start'){
      const state=f.armed();delete state.autonomy_session;return reply(state);
    }
  });
  await f.control.startAutonomy();
  assert.ok(!f.calls.some(body=>body?.action==='autonomy_heartbeat'));
  assert.equal(f.calls.at(-1).action,'stop');assert.equal(f.control.state.error,'missing_autonomy_session');
  assert.equal(f.control.autoSession,null);
});

test('fresh page observes a running owner and even a leaked GET token cannot be adopted',async()=>{
  const f=autoFixture();Object.assign(f.server,f.armed());f.server.state='driving';f.server.outputs=[140,140,140,140];
  // Deliberately violate the server contract in the fixture: frontend must still never take ownership.
  f.handle(body=>body===null?reply(f.snapshot()):undefined);
  await f.control.refresh();await f.control.refresh();await f.control.startAutonomy();await pause(20);
  assert.equal(f.control.autoSession,null);assert.equal(f.control.session,null);assert.equal(f.control.state.autonomy_session,undefined);
  assert.ok(f.calls.every(body=>body===null));assert.equal(f.control.canStartAutonomy(),false);
});

for(const mode of ['automatic','manual']){
  test(`an observing ${mode} page GET failure never sends global STOP or invents zero outputs`,async()=>{
    const f=autoFixture({mode});Object.assign(f.server,f.armed());
    f.server.state='driving';f.server.outputs=mode==='automatic'?[140,140,140,140]:[200,200,200,200];
    f.server.autonomy.running=mode==='automatic';await f.control.refresh();
    assert.equal(f.control.autoSession,null);assert.equal(f.control.session,null);
    if(mode==='automatic')assert.equal(f.control.state.autonomy.running,true);
    const lastOutputs=[...f.control.state.outputs];
    f.handle(body=>{if(body===null)throw Error('observer GET disconnected');});
    await f.control.refresh();
    assert.ok(f.calls.every(body=>body===null));assert.equal(f.control.state.state,'offline');
    assert.equal(f.control.state.reason,'connection_lost');assert.deepEqual(f.control.state.outputs,lastOutputs);
    assert.equal(f.control.autoSession,null);assert.equal(f.control.session,null);
  });
}

test('GET failure with a locally owned idle motor session still actively requests global STOP',async()=>{
  const f=autoFixture({mode:'manual'});await f.control.refresh();f.control.session='local-idle-motor-owner';
  f.handle(body=>{if(body===null)throw Error('owned GET disconnected');});
  await f.control.refresh();
  assert.equal(f.calls.at(-1).action,'stop');assert.equal(f.control.session,null);
  assert.equal(f.control.state.reason,'connection_lost');assert.deepEqual(f.control.state.outputs,[0,0,0,0]);
});

test('backend termination drops the auto session and polling cannot restart it',async()=>{
  const f=autoFixture();await f.control.refresh();
  f.handle(body=>body?.action==='autonomy_heartbeat'?reply({...f.snapshot(),armed:false,state:'stopped',
    autonomy:{ready:true,running:false,state:'stopped',reason:'heartbeat_expired'}}):undefined);
  await f.control.startAutonomy();assert.equal(f.control.autoSession,null);assert.equal(f.control.autoTimer,null);
  const count=f.calls.filter(body=>body?.action==='autonomy_start').length;
  await f.control.refresh();await pause(20);
  assert.equal(f.calls.filter(body=>body?.action==='autonomy_start').length,count);
});

test('autonomy status explains ready, straight, turns, lost line, staleness, and observer mode',()=>{
  const {autonomyLabel}=require('../control.js');
  const status=(state,reason=null)=>autonomyLabel({state:'stopped',autonomy:{ready:true,running:false,state,reason}});
  assert.match(status('ready'),/准备就绪/);assert.match(status('straight'),/直行/);
  for(const state of ['left','soft_left','turn_left'])assert.match(status(state),/左转/);
  for(const state of ['right','soft_right','turn_right'])assert.match(status(state),/右转/);
  assert.match(status('lost_line'),/丢线停车/);assert.match(status('sensor_stale'),/数据过期/);
  assert.match(status('stopped','lost_line'),/丢线停车/);assert.match(status('stopped','sensor_stale'),/数据过期/);
  assert.match(autonomyLabel({state:'offline'}),/等待连接/);
  assert.match(autonomyLabel({state:'driving',autonomy:{running:true,state:'straight'}}),/其他页面控制中/);
});

test('mode switch while a heartbeat is pending rejects its late automatic mode and outputs',async()=>{
  const f=autoFixture();await f.control.refresh();const pending=deferred();
  f.handle(body=>body?.action==='autonomy_heartbeat'?pending.promise:undefined);
  const start=f.control.startAutonomy();await flush();await f.control.setMode('manual');
  const count=f.calls.length;pending.resolve(reply({...f.armed(),state:'driving',outputs:[140,140,140,140]}));
  await start;await pause(20);assert.equal(f.calls.length,count);
  assert.equal(f.control.state.driving_mode,'manual');assert.equal(f.control.autoSession,null);
  assert.deepEqual(f.control.state.outputs,[0,0,0,0]);
});

test('late observed GET cannot restore an owner or outputs after STOP',async()=>{
  const f=autoFixture();await f.control.refresh();const pending=deferred();
  f.handle(body=>body===null?pending.promise:undefined);
  const refresh=f.control.refresh();await flush();await f.control.stop();
  pending.resolve(reply({...f.armed(),state:'driving',outputs:[140,140,140,140]}));await refresh;
  assert.equal(f.control.autoSession,null);assert.equal(f.control.state.state,'stopped');
  assert.deepEqual(f.control.state.outputs,[0,0,0,0]);
});

test('STOP retains the last confirmed outputs until its response, including failed acknowledgement',async()=>{
  const f=autoFixture();await f.control.refresh();await f.control.startAutonomy();const pending=deferred();
  f.handle(body=>body?.action==='stop'?pending.promise:undefined);
  const stop=f.control.stop();assert.equal(f.control.state.state,'stopping');
  assert.deepEqual(f.control.state.outputs,[140,140,140,140]);assert.equal(f.control.autoSession,null);
  pending.reject(Error('STOP acknowledgement lost'));await stop;
  assert.equal(f.control.state.state,'offline');assert.deepEqual(f.control.state.outputs,[140,140,140,140]);
});

test('a synchronous stop in the status callback cannot leave another auto timer scheduled',async()=>{
  const f=autoFixture();await f.control.refresh();let stopped;
  f.control.change=state=>{if(state.state==='driving')stopped=f.control.stop();};
  await f.control.startAutonomy();await stopped;
  assert.equal(f.control.autoTimer,null);assert.equal(f.control.autoSession,null);const count=f.calls.length;
  await pause(20);assert.equal(f.calls.length,count);
});

// Minimal DOM/VM exercises the real mount and its existing buttons without a browser or network.
function mountedFixture(f=autoFixture()) {
  const vm=require('node:vm'),fs=require('node:fs'),path=require('node:path');
  function node(){
    const listeners=new Map();
    return {disabled:false,hidden:false,textContent:'',value:'200',dataset:{},className:'',
      classList:{toggle(){}},setAttribute(){},setPointerCapture(){},closest(){return null;},
      addEventListener(type,fn){if(!listeners.has(type))listeners.set(type,[]);listeners.get(type).push(fn);},
      dispatchEvent(event){for(const fn of listeners.get(event.type)||[])fn(event);return true;},
      fire(type,props={}){this.dispatchEvent({type,target:this,preventDefault(){},...props});}};
  }
  const ids=['mode-manual','mode-auto','manual-controls','auto-controls','camera-title','autonomy-status','auto-start','auto-stop',
    'drive-speed','drive-status','drive-message','drive-pwm','drive-stop','drive-speed-value'];
  const nodes=Object.fromEntries(ids.map(id=>[id,node()]));
  const buttons=['forward','backward','left','right'].map(direction=>Object.assign(node(),{dataset:{drive:direction}}));
  const document=Object.assign(node(),{hidden:false,getElementById:id=>nodes[id]||null,querySelectorAll:()=>buttons});
  const window=node();let timerId=0;const timers=new Map(),intervals=new Map();
  const context=vm.createContext({fetch:f.fetch,document,window,AbortController,performance,console,
    CustomEvent:class {constructor(type,{detail}){this.type=type;this.detail=detail;}},
    setTimeout:(fn,delay)=>{const id=++timerId;timers.set(id,{fn,delay});return id;},clearTimeout:id=>timers.delete(id),
    setInterval:(fn,delay)=>{const id=++timerId;intervals.set(id,{fn,delay});return id;},clearInterval:id=>intervals.delete(id)});
  vm.runInContext(fs.readFileSync(path.join(__dirname,'../control.js'),'utf8'),context);
  return {...f,nodes,buttons,document,window,timers,intervals,
    tick:async delay=>{for(const [id,timer] of [...timers])if(timer.delay===delay){timers.delete(id);timer.fn();}await flush();},
    close:async()=>{window.fire('pagehide');await flush();}};
}

test('mounted automatic selection never starts; only existing start button handshakes',async()=>{
  const f=mountedFixture(autoFixture({mode:'manual'}));await flush();
  try {
    assert.equal(f.nodes['drive-speed'].value,'200');f.nodes['mode-auto'].fire('click');await flush();
    assert.equal(f.nodes['auto-start'].disabled,false);assert.equal(f.calls.filter(body=>body?.action==='autonomy_start').length,0);
    assert.match(f.nodes['drive-message'].textContent,/基础循线（非完整自主房间任务）/);
    assert.match(f.nodes['drive-message'].textContent,/黑=0、地板=1/);assert.equal(f.nodes['drive-message'].hidden,false);
    f.nodes['auto-start'].fire('click');await flush();
    assert.equal(f.calls.filter(body=>body?.action==='autonomy_start').length,1);
    assert.equal(f.calls.filter(body=>body?.action==='autonomy_heartbeat').length,1);
    assert.match(f.nodes['autonomy-status'].textContent,/直行/);
    await f.tick(200);assert.deepEqual(f.calls.filter(body=>body?.action==='autonomy_heartbeat').map(body=>body.seq),[1,2]);
  } finally {await f.close();}
});

for(const event of ['blur','hidden','pagehide']){
  test(`mounted ${event} during a pending start rejects the late response`,async()=>{
    const backend=autoFixture();const pending=deferred();backend.handle(body=>body?.action==='autonomy_start'?pending.promise:undefined);
    const f=mountedFixture(backend);await flush();f.nodes['auto-start'].fire('click');await flush();
    if(event==='hidden'){f.document.hidden=true;f.document.fire('visibilitychange');}else f.window.fire(event);
    await flush();pending.resolve(reply(f.armed()));await flush();await f.tick(200);
    assert.ok(!f.calls.some(body=>body?.action==='autonomy_heartbeat'));assert.equal(f.calls.at(-1).action,'stop');
    assert.equal([...f.timers.values()].filter(timer=>timer.delay===200).length,0);
    await f.close();
  });
}

for(const event of ['blur','hidden','pagehide']){
  test(`mounted ${event} cancels an active owner's heartbeat`,async()=>{
    const f=mountedFixture();await flush();f.nodes['auto-start'].fire('click');await flush();
    if(event==='hidden'){f.document.hidden=true;f.document.fire('visibilitychange');}else f.window.fire(event);
    await flush();const count=f.calls.length;await f.tick(200);
    assert.equal(f.calls.length,count);assert.equal(f.calls.at(-1).action,'stop');
    assert.equal([...f.timers.values()].filter(timer=>timer.delay===200).length,0);
    await f.close();
  });
}

for(const code of ['Space','Escape']){
  test(`mounted ${code} emergency stop cancels auto even with heartbeat in flight`,async()=>{
    const backend=autoFixture();const pending=deferred();backend.handle(body=>body?.action==='autonomy_heartbeat'?pending.promise:undefined);
    const f=mountedFixture(backend);await flush();f.nodes['auto-start'].fire('click');await flush();
    f.window.fire('keydown',{code});await flush();
    pending.resolve(reply({...f.armed(),state:'driving',outputs:[140,140,140,140]}));await flush();
    const count=f.calls.length;await f.tick(200);assert.equal(f.calls.length,count);
    assert.equal(f.calls.at(-1).action,'stop');assert.equal(f.nodes['drive-pwm'].textContent,'M1 0   ·   M2 0   ·   M3 0   ·   M4 0');
    await f.close();
  });
}

test('mounted stop button, hidden start, refresh and another tab cannot acquire heartbeats',async()=>{
  const f=mountedFixture();await flush();f.document.hidden=true;f.nodes['auto-start'].fire('click');await flush();
  assert.ok(f.calls.every(body=>body===null));f.document.hidden=false;f.nodes['auto-start'].fire('click');await flush();
  f.nodes['auto-stop'].fire('click');await flush();const count=f.calls.length;await f.tick(200);assert.equal(f.calls.length,count);
  await f.close();
  const observer=autoFixture();Object.assign(observer.server,observer.armed());observer.server.autonomy.state='straight';
  const page=mountedFixture(observer);await flush();
  assert.equal(page.nodes['auto-start'].disabled,true);assert.match(page.nodes['autonomy-status'].textContent,/其他页面控制中/);
  assert.match(page.nodes['drive-message'].textContent,/仅观测，不接管心跳/);
  for(const {fn} of page.intervals.values())fn();await flush();
  observer.handle(body=>{if(body===null)throw Error('observer GET disconnected');});
  for(const {fn} of page.intervals.values())fn();await flush();
  assert.match(page.nodes['autonomy-status'].textContent,/等待连接/);
  assert.doesNotMatch(page.nodes['drive-message'].textContent,/已尝试停车|已物理停车/);
  page.window.fire('blur');await flush();assert.ok(page.calls.every(body=>body===null));await page.close();
});

test('mounted held keys keep manual speed 200, release stops, and mode switch clears held state',async()=>{
  const backend=autoFixture({mode:'manual'});const f=mountedFixture(backend);await flush();
  // Manual protocol responses remain separate from the auto-only ownership credential.
  backend.handle(body=>{
    if(body?.action==='arm')return reply({...backend.snapshot(),armed:true,state:'armed',session:'manual-owner'});
    if(body?.action==='drive')return reply({...backend.snapshot(),armed:true,state:'driving',outputs:[200,200,200,200]});
    if(body?.action==='release')return reply({...backend.snapshot(),armed:true,state:'armed',outputs:[0,0,0,0]});
  });
  try {
    f.window.fire('keydown',{code:'KeyW'});await flush();assert.equal(f.calls.at(-1).speed,200);
    await f.tick(100);assert.equal(f.calls.at(-1).action,'drive');
    f.window.fire('keyup',{code:'KeyW'});await flush();assert.equal(f.calls.at(-1).action,'release');
    const count=f.calls.length;await f.tick(100);assert.equal(f.calls.length,count);
    f.window.fire('keydown',{code:'ArrowLeft'});await flush();f.nodes['mode-auto'].fire('click');await flush();
    const afterSwitch=f.calls.length;f.window.fire('keyup',{code:'ArrowLeft'});await flush();
    assert.equal(f.calls.length,afterSwitch);assert.equal(f.calls.at(-1).action,'set_mode');
  } finally {await f.close();}
});

test('mounted heartbeat timeout actively sends STOP and schedules no replacement',async()=>{
  const backend=autoFixture();backend.handle((body,request)=>{
    if(body?.action==='autonomy_heartbeat')return new Promise((_,reject)=>request.signal.addEventListener('abort',()=>reject(Error('timeout'))));
  });
  const f=mountedFixture(backend);await flush();f.nodes['auto-start'].fire('click');await flush();
  await f.tick(800);assert.equal(f.calls.at(-1).action,'stop');
  const count=f.calls.length;await f.tick(200);assert.equal(f.calls.length,count);
  assert.match(f.nodes['drive-message'].textContent,/不能确认实物已停/);await f.close();
});
