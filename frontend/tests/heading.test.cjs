'use strict';
const {test}=require('node:test');
const assert=require('node:assert/strict');
const {headingText}=require('../heading.js');
const {autonomyLabel}=require('../control.js');
test('heading display uses real raw angle before zero, relative angle after zero, and no stale number',()=>{
  assert.equal(headingText({status:'live',calibrated:false,yaw_deg:-26.123}),'−26.1'.replace('−','-'));
  assert.equal(headingText({status:'live',calibrated:true,heading_deg:180.12,yaw_deg:-26}),'180.1');
  assert.equal(headingText({status:'live',calibrated:true,heading_deg:-.01}),'0.0');
  assert.equal(headingText({status:'unavailable',calibrated:true,heading_deg:90}),'—');
});
test('IMU turn shows angle action, post-turn all-white shows stopped',()=>{
  assert.match(autonomyLabel({autonomy:{running:true,reason:'imu_turn_left'}},true),/左转90/);
  assert.match(autonomyLabel({autonomy:{running:false,reason:'line_not_found_after_turn'}},true),/未压线.*停止/);
});
test('mounted heading polls read-only and calibration sends only the zero action',async()=>{
  const vm=require('node:vm'),fs=require('node:fs');
  const listeners={},calls=[],value={textContent:''},button={disabled:true,textContent:'校准',addEventListener:(name,fn)=>listeners['button-'+name]=fn};
  const document={hidden:false,getElementById:id=>id==='heading-value'?value:button,addEventListener:(name,fn)=>listeners[name]=fn};
  const imu={status:'live',calibrated:false,yaw_deg:50};
  vm.runInNewContext(fs.readFileSync(require.resolve('../heading.js'),'utf8'),{
    document,window:{addEventListener(){}},AbortController,setInterval:()=>1,clearInterval(){},setTimeout,clearTimeout,
    fetch:async(path,opt)=>{calls.push([path,opt]);const data=opt.body?{imu:{status:'live',calibrated:true,heading_deg:0}}:path.endsWith('/imu')?imu:{control_key:'fixture-key'};return {ok:true,json:async()=>({data})};}
  });
  await new Promise(resolve=>setImmediate(resolve));
  assert.equal(value.textContent,'50.0');assert.equal(button.disabled,false);
  await listeners['button-click']();assert.equal(value.textContent,'0.0');
  const posts=calls.filter(([,o])=>o.method==='POST');assert.equal(posts.length,1);
  assert.equal(posts[0][0],'/api/v1/control');assert.deepEqual(JSON.parse(posts[0][1].body),{action:'imu_calibrate'});
  listeners['rdk-motion']({detail:{armed:true}});assert.equal(button.disabled,true);
});
