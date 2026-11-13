const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs'),vm=require('node:vm');
const flush=async()=>{for(let i=0;i<15;i++)await Promise.resolve();};
function fixture(){
 let id=0,active=0,maxActive=0,first=true;const timers=new Map(),pending=[],calls=[];
 const context={module:{exports:{}},AbortController,performance,
  setTimeout:(fn,delay)=>{timers.set(++id,{fn,delay});return id;},clearTimeout:n=>timers.delete(n)};
 vm.runInNewContext(fs.readFileSync(require.resolve('../control.js'),'utf8'),context);
 const reply=data=>({ok:true,json:async()=>({data})});
 const fetch=async(_url,options)=>{
  const body=options.body?JSON.parse(options.body):null;calls.push(body);
  if(!body)return reply({enabled:true,driving_mode:'manual',control_key:'k',armed:false});
  if(body.action==='arm')return reply({armed:true,session:'fixture'});
  if(body.action!=='drive')return reply({armed:false,outputs:[0,0,0,0]});
  if(first){first=false;return reply({armed:true});}
  active++;maxActive=Math.max(maxActive,active);
  return new Promise(resolve=>pending.push({body,resolve:()=>{active--;resolve(reply({armed:true}));}}));
 };
 const control=new context.module.exports.WheelControl({fetch});
 return {control,calls,pending,timers,get maxActive(){return maxActive;},
  fire:async delay=>{const e=[...timers].find(([,t])=>t.delay===delay);assert.ok(e,`missing ${delay}ms timer`);timers.delete(e[0]);e[1].fn();await flush();}};
}
test('steering during a timer-fired pending drive cannot start a second drive request',async()=>{
 const f=fixture();await f.control.refresh();await f.control.start('forward',200);
 await f.fire(100);assert.equal(f.pending.length,1);
 const changed=f.control.start('forward_left',200);await flush();
 assert.equal(f.maxActive,1,'the already-fired timer must not allow a parallel steering request');
 f.pending.shift().resolve();await flush();await f.fire(0);
 assert.equal(f.pending[0].body.direction,'forward_left');
 f.pending.shift().resolve();await changed;await flush();await f.control.stop();
});
test('left-right changes during the pending request coalesce to the latest held combination',async()=>{
 const f=fixture();await f.control.refresh();await f.control.start('forward',200);await f.fire(100);
 f.control.start('forward_left',200);f.control.start('forward',200);f.control.start('forward_right',200);await flush();
 assert.equal(f.maxActive,1);f.pending.shift().resolve();await flush();await f.fire(0);
 assert.equal(f.pending[0].body.direction,'forward_right');f.pending.shift().resolve();await flush();await f.control.stop();
});
