const {test}=require('node:test');
const assert=require('node:assert/strict');
const {sensorLive,dhtDisplay,sparklinePath,FramePoller,frameDelay}=require('../app.js');
const pause=ms=>new Promise(r=>setTimeout(r,ms));

test('permanent upside-down camera mounting rotates only the preview image',()=>{
  const fs=require('node:fs');
  const path=require('node:path');
  const css=fs.readFileSync(path.join(__dirname,'../styles.css'),'utf8');
  const html=fs.readFileSync(path.join(__dirname,'../index.html'),'utf8');
  const image=css.match(/\.camera-stage img\{([^}]*)\}/);
  assert.ok(image,'camera image rule exists');
  assert.match(image[1],/(?:^|;)transform:rotate\(180deg\)(?:;|$)/);
  assert.match(image[1],/(?:^|;)transform-origin:center(?:;|$)/);
  assert.match(image[1],/object-fit:contain/);
  assert.match(html,/<link rel="stylesheet" href="\/styles\.css">/);
  for(const selector of ['camera-stage','camera-overlay','camera-live','camera-frame-age']){
    const rule=css.match(new RegExp('\\.'+selector+'\\{([^}]*)\\}'));
    assert.ok(rule,selector);
    assert.doesNotMatch(rule[1],/(?:^|;)transform:/,selector+' must remain upright');
  }
});

test('each sensor has its own expiry and invalid readings never become live',()=>{
  assert.equal(sensorLive({status:'live',age_seconds:1,name:'line_sensors'},0),true);
  assert.equal(sensorLive({status:'live',age_seconds:1,name:'line_sensors'},2),false);
  assert.equal(sensorLive({status:'live',age_seconds:4,name:'dht11'},0),true);
  assert.equal(sensorLive({status:'error',age_seconds:0,name:'dht11'},0),false);
  assert.equal(sensorLive({status:'live',age_seconds:null,name:'camera'},0),false);
});
function dhtFixture(status='live'){
  const value={temperature_c:21.7,humidity_pct:48};
  return {name:'dht11',status,value:status==='live'?value:null,error:status==='error'?'checksum mismatch':null,
    monotonic:status==='live'?100:102.5,age_seconds:.5,last_good:{value,monotonic:100}};
}
test('brief DHT retry keeps the same numbers and badge with an honest sample age',()=>{
  const current=dhtDisplay(dhtFixture()), failed=dhtFixture('error'), retry=dhtDisplay(failed);
  assert.deepEqual(retry.value,current.value);
  assert.equal(retry.label,current.label);assert.equal(retry.label,'持续采样');
  assert.equal(retry.tone,current.tone);assert.equal(retry.cached,true);
  assert.equal(retry.age,3);assert.match(retry.note,/3秒前更新/);
  assert.match(retry.title,/本轮读取未成功/);assert.match(retry.title,/checksum mismatch/);
  assert.equal(failed.status,'error');assert.equal(failed.value,null);
  assert.equal(sensorLive(failed),false,'raw sensor validity is not relaxed');
});
test('continued DHT failures age the last success rather than each new failed attempt',()=>{
  const failed=dhtFixture('error');failed.monotonic=109;
  const view=dhtDisplay(failed);
  assert.equal(view.available,false);assert.equal(view.value,null);
  assert.equal(view.label,'读数已过期');assert.notEqual(view.tone,'live');
  assert.match(view.note,/上次有效 21.7°C/);
});
test('DHT retries expire at the existing nine-second limit including browser elapsed time',()=>{
  assert.equal(dhtDisplay(dhtFixture('error'),6).available,true);
  assert.equal(dhtDisplay(dhtFixture('error'),6.01).available,false);
  assert.equal(dhtDisplay(dhtFixture(),9).available,false);
});
test('missing age and future last-good timestamps never become valid DHT cache',()=>{
  const failed=dhtFixture('error');failed.age_seconds=null;
  assert.equal(dhtDisplay(failed).available,false);
  failed.age_seconds=.5;failed.last_good.monotonic=200;
  assert.equal(dhtDisplay(failed).available,false);
  assert.equal(dhtDisplay(dhtFixture('error'),-1).available,false);
});
test('missing and nonnumeric DHT readings never become zero or live',()=>{
  const failed=dhtFixture('error');failed.last_good.value={temperature_c:null,humidity_pct:48};
  assert.equal(dhtDisplay(failed).available,false);
  assert.equal(dhtDisplay(failed).value,null);
  const current=dhtFixture();current.value={temperature_c:21.7,humidity_pct:NaN};
  assert.equal(dhtDisplay(current).available,false);
  assert.equal(dhtDisplay({}).value,null);
});
test('real zero-valued DHT measurements are preserved',()=>{
  const current=dhtFixture();current.value={temperature_c:0,humidity_pct:0};
  assert.deepEqual(dhtDisplay(current).value,{temperature_c:0,humidity_pct:0});
});
test('pause and lost connection never label held DHT data as live',()=>{
  const paused=dhtDisplay(dhtFixture('error'),0,{active:false,phase:'paused'});
  assert.equal(paused.label,'已暂停');assert.equal(paused.tone,'stale');assert.match(paused.note,/旧读数/);
  const offline=dhtDisplay(dhtFixture(),0,{active:false,phase:'error'});
  assert.equal(offline.label,'旧读数');assert.equal(offline.tone,'stale');
});
test('DHT without a first valid sample still reports a real read failure',()=>{
  const failed=dhtFixture('error');failed.last_good=null;
  const view=dhtDisplay(failed);assert.equal(view.label,'读取异常');assert.equal(view.tone,'error');
  assert.equal(view.value,null);assert.match(view.title,/checksum mismatch/);
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
