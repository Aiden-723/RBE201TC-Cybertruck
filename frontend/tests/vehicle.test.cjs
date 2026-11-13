const {test}=require('node:test');
const assert=require('node:assert/strict');
const {motionView}=require('../vehicle.js');
const state=outputs=>({outputs,enabled:true,armed:true,state:'driving',lease_ms:450,last_drive_age_seconds:0});
test('forward, reverse, pivot and arcs retain four independent signed commands',()=>{
 for(const [v,label] of [[[200,200,200,200],'前进'],[[-200,-200,-200,-200],'后退'],[[200,200,-200,-200],'原地右转'],[[-200,-200,200,200],'原地左转'],[[200,200,0,0],'前进 · 右转'],[[0,0,200,200],'前进 · 左转'],[[0,0,-200,-200],'后退 · 右转'],[[-200,-200,0,0],'后退 · 左转']]){
  const result=motionView(state(v),10);assert.deepEqual(result.wheels,v);assert.equal(result.label,label);
 }
});
test('unknown front/rear mapping never guesses a single wheel',()=>{
 const result=motionView(state([200,0,0,0]),0);assert.equal(result.ambiguous,true);assert.deepEqual(result.wheels,[0,0,0,0]);assert.deepEqual(result.outputs,[200,0,0,0]);
 assert.deepEqual(motionView(state([200,0,0,100]),0,[1,2]).wheels,[0,200,0,100]);
 assert.deepEqual(motionView(state([200,0,0,100]),0,[0,3]).wheels,[200,0,100,0]);
});
test('fault, offline, unarmed, expired and malformed snapshots stop the animation',()=>{
 const live=state([200,200,200,200]);
 for(const s of [{...live,fault:'serial'},{...live,state:'offline'},{...live,armed:false},{...live,enabled:false},{...live,last_drive_age_seconds:null},{...live,outputs:[200,NaN,200,200]},{...live,outputs:[200,200]},{...live,outputs:[Infinity,0,0,0]}])assert.deepEqual(motionView(s,0).wheels,[0,0,0,0]);
 assert.deepEqual(motionView(live,450).wheels,[0,0,0,0]);
 assert.deepEqual(motionView({...live,last_drive_age_seconds:.3},150).wheels,[0,0,0,0]);
 assert.deepEqual(motionView(live,-1).wheels,[0,0,0,0]);
 assert.deepEqual(motionView({...live,state:'stopped',outputs:[0,0,0,0]},1501).wheels,[0,0,0,0]);
});
test('animation follows external controllers without a browser-owned session',()=>{
 const v=motionView(state([-80,-80,220,220]),40);assert.deepEqual(v.wheels,[-80,-80,220,220]);assert.equal(v.online,true);
});
