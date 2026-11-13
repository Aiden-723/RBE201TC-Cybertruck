const {test}=require('node:test');
const assert=require('node:assert/strict');
const {sensorLive,sparklinePath,FramePoller,frameDelay}=require('../app.js');
const pause=ms=>new Promise(r=>setTimeout(r,ms));

test('each sensor has its own expiry and invalid readings never become live',()=>{
  assert.equal(sensorLive({status:'live',age_seconds:1,name:'line_sensors'},0),true);
  assert.equal(sensorLive({status:'live',age_seconds:1,name:'line_sensors'},2),false);
  assert.equal(sensorLive({status:'live',age_seconds:4,name:'dht11'},0),true);
  assert.equal(sensorLive({status:'error',age_seconds:0,name:'dht11'},0),false);
  assert.equal(sensorLive({status:'live',age_seconds:null,name:'camera'},0),false);
});
test('sparklines require real finite samples and handle a flat series',()=>{
  assert.equal(sparklinePath([], 'temperature_c'),'');
  assert.equal(sparklinePath([{temperature_c:27}], 'temperature_c'),'');
  assert.match(sparklinePath([{temperature_c:27},{temperature_c:27}], 'temperature_c'),/^M[\d.,]+ L[\d.,]+$/);
  assert.doesNotMatch(sparklinePath([{temperature_c:NaN},{temperature_c:27},{temperature_c:28}], 'temperature_c'),/NaN/);
});
test('camera requests do not overlap and late frames after pause are discarded',async()=>{
  let finish, calls=0, frames=0;
  const camera=new FramePoller({fetch:()=>{calls++;return new Promise(r=>finish=r);},frame:()=>frames++,interval:5});
  camera.setEnabled(true);camera.refresh();
  assert.equal(calls,1);
  camera.setEnabled(false);
  finish({ok:true,headers:{get:()=> 'image/jpeg'},blob:async()=>({size:8})});
  await pause(10);
  assert.equal(frames,0);assert.equal(calls,1);
});
test('camera authentication is header-only and failed frames are not displayed',async()=>{
  let seen, errors=0, frames=0;
  const camera=new FramePoller({token:()=> 'test-only',fetch:async(url,options)=>{seen={url,options};return {ok:false};},frame:()=>frames++,error:()=>errors++,interval:100000});
  camera.setEnabled(true);await pause(5);camera.setEnabled(false);
  assert.equal(seen.url,'/api/v1/camera/frame');
  assert.equal(seen.options.headers.Authorization,'Bearer test-only');
  assert.equal(frames,0);assert.equal(errors,1);
});
test('frame waits use an absolute deadline and drop missed slots',()=>{
  assert.ok(Math.abs(frameDelay(1000/15,25)-41.6666667)<0.001);
  assert.equal(frameDelay(1000/15,100),0);
});
test('cached copies of the same acquired frame are not counted as new frames',async()=>{
  let id='frame-one',frames=0;
  const camera=new FramePoller({interval:100000,fetch:async()=>({ok:true,headers:{get:name=>name==='X-Frame-Id'?id:'image/jpeg'},blob:async()=>({size:10})}),frame:()=>frames++});
  camera.enabled=true;
  await camera.refresh();await camera.refresh();
  assert.equal(frames,1);
  id='frame-two';await camera.refresh();
  camera.setEnabled(false);
  assert.equal(frames,2);
});
