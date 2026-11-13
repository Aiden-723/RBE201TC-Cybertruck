const {test}=require('node:test');const assert=require('node:assert/strict');
const {TrialsClient,TRIAL_RESULTS}=require('../trials.js');
function response(data,ok=true){return {ok,json:async()=>ok?{data}:{error:{code:'save_failed'}}};}
test('trial monitor reads only and keeps the annotation key in memory',async()=>{
  const calls=[];const c=new TrialsClient({fetch:async(path,options)=>{calls.push({path,options});return response({enabled:true,records:[],annotation_key:'fixture-key'});}});
  await c.refresh();assert.equal(calls.length,1);assert.equal(calls[0].options.method,'GET');assert.equal(calls[0].path,'/api/v1/line-trials');assert.equal(c.key,'fixture-key');assert.equal(c.state.annotation_key,undefined);
});
test('preset result and blank custom text require an explicit user choice',async()=>{
  assert.ok(TRIAL_RESULTS.passed);assert.ok(TRIAL_RESULTS.custom);let calls=0;
  const c=new TrialsClient({fetch:async()=>{calls++;return response({enabled:true,records:[]});}});
  await assert.rejects(c.save('id','',''));await assert.rejects(c.save('id','custom','  '));assert.equal(calls,0);
});
test('saving custom result sends text verbatim only to the feedback endpoint',async()=>{
  const calls=[];const c=new TrialsClient({fetch:async(path,options)=>{calls.push({path,options});return response({enabled:true,records:[],annotation_key:'key'});}});c.key='key';
  const note='只转了约70度 <literal>';await c.save('line_fixture','custom',note);
  assert.equal(calls[0].path,'/api/v1/line-trials');assert.equal(calls[0].options.headers['X-Control-Key'],'key');
  assert.deepEqual(JSON.parse(calls[0].options.body),{trial_id:'line_fixture',category:'custom',note});
  assert.ok(calls.every(c=>c.path!=='/api/v1/control'));
});
test('failed save is not optimistically accepted and late old-token reads are fenced',async()=>{
  const c=new TrialsClient({fetch:async()=>response(null,false)});await assert.rejects(c.save('id','passed',''));
  let finish;c.fetch=()=>new Promise(r=>finish=r);const reading=c.refresh();c.setToken('new');finish(response({enabled:true,records:[{id:'old'}],annotation_key:'old-key'}));await reading;
  assert.deepEqual(c.state.records,[]);assert.equal(c.key,'');
});
test('the active page removes driving history, feedback prompts and their loader',()=>{
  const fs=require('node:fs'),path=require('node:path');const html=fs.readFileSync(path.join(__dirname,'../index.html'),'utf8');
  assert.doesNotMatch(html,/trial-history|trial-dialog|trial-result|trial-note|trial-save|trial-status|\/trials\.(js|css)/);
  for(const id of ['auto-start','auto-stop','heading-calibrate','acquisition-panel'])assert.ok(html.includes('id="'+id+'"'));
});

test('mounted UI prompts only after stop, saves custom text, and retains deferred history',async()=>{
  const fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
  class Element{
    constructor(){this.value='';this.textContent='';this.children=[];this.listeners={};this.open=false;this.shows=0;}
    append(...nodes){this.children.push(...nodes);}replaceChildren(...nodes){this.children=nodes;}
    addEventListener(name,fn){(this.listeners[name]??=[]).push(fn);}focus(){}
    showModal(){this.open=true;this.shows++;}close(){this.open=false;}
    async emit(name,event={}){for(const fn of this.listeners[name]||[])await fn(event);}
  }
  const elements={};const get=id=>elements[id]??=new Element();const listeners={};let tick;let records=[],active=null;const requests=[];
  const document={hidden:false,getElementById:get,createElement:()=>new Element(),addEventListener:(name,fn)=>{(listeners[name]??=[]).push(fn);}};
  const context={document,window:{addEventListener(){}},module:{exports:{}},AbortController,setTimeout,clearTimeout,setInterval:fn=>{tick=fn;return 1;},clearInterval(){},URL,Blob,
    fetch:async(url,options)=>{requests.push({url,options});if(options.method==='POST'){const data=JSON.parse(options.body);records=records.map(r=>r.id===data.trial_id?{...r,result:{category:data.category,label:TRIAL_RESULTS[data.category],note:data.note}}:r);return response(records[0]);}
      return response({enabled:true,records:structuredClone(records),active_id:active,annotation_key:'fixture'});}};
  vm.runInNewContext(fs.readFileSync(path.join(__dirname,'../trials.js'),'utf8'),context);
  const turn=()=>new Promise(r=>setImmediate(r));await turn();
  const motion=detail=>{for(const fn of listeners['rdk-motion'])fn({detail});};
  const first={id:'line_one',started_at:'2026-11-13T00:00:00Z',status:'running',duration_s:2,result:null};
  records=[first];active=first.id;motion({state:'driving',armed:true,outputs:[50,50,50,50]});tick();await turn();assert.equal(get('trial-dialog').shows,0);
  records=[{...first,status:'finished'}];active=null;motion({state:'stopped',armed:false,outputs:[0,0,0,0]});tick();await turn();
  assert.equal(get('trial-dialog').shows,1);assert.equal(get('trial-result').value,'');assert.equal(get('trial-note').value,'');assert.equal(get('trial-save').disabled,true);
  get('trial-result').value='custom';await get('trial-result').emit('change');get('trial-note').value='约70度停下，左轮先动';await get('trial-note').emit('input');assert.equal(get('trial-save').disabled,false);
  await get('trial-form').emit('submit',{preventDefault(){}});assert.equal(get('trial-dialog').open,false);assert.equal(records[0].result.note,'约70度停下，左轮先动');
  tick();await turn();assert.equal(get('trial-dialog').shows,1);
  records=[{...first,id:'line_two',status:'finished'},...records];tick();await turn();assert.equal(get('trial-dialog').shows,2);
  await get('trial-later').emit('click');tick();await turn();assert.equal(get('trial-dialog').shows,2);assert.equal(get('trial-history-body').children.length,2);
  assert.ok(requests.every(r=>r.url.startsWith('/api/v1/line-trials')));
});
