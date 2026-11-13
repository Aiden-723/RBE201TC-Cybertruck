'use strict';
// Continuous UI fixtures only; no hardware requests or ownership creation.
const {test}=require('node:test');
const assert=require('node:assert/strict');
const {autonomyLabel,WheelControl}=require('../control.js');
for(const [direction,label] of [['left','左'],['right','右']]){
  test(`current ${direction} outer evidence is correction, not an angle maneuver`,()=>{
    const state={state:'driving',autonomy:{state:direction,reason:'hard_'+direction,running:true,ready:true}};
    assert.match(autonomyLabel(state,true),new RegExp(label+'外侧见线.*强纠偏'));
    assert.match(autonomyLabel(state,false),/其他页面控制中/);
    assert.doesNotMatch(autonomyLabel(state,true),/直角|90|完成|停转确认/);
  });
}
test('only sustained actual line loss reports a timeout, not turn completion',()=>{
  const text=autonomyLabel({state:'stopped',autonomy:{ready:true,running:false,state:'lost_line',reason:'line_gap_timeout'}});
  assert.match(text,/持续不可定位.*请求停车/);assert.doesNotMatch(text,/准备就绪|转弯完成|90/);
});
test('the first center snapshot already means continuing forward',()=>{
  const text=autonomyLabel({state:'driving',outputs:[84,84,84,84],autonomy:{ready:true,running:true,state:'straight',reason:'center'}},true);
  assert.match(text,/居中.*继续前进/);assert.doesNotMatch(text,/等待|确认|停止/);
});
test('tracking snapshots never create a page owner or bypass explicit start',()=>{
  const c=new WheelControl({fetch:async()=>{throw Error('SOFTWARE_FIXTURE_NO_NETWORK');}});
  c.emit({enabled:true,state:'stopped',driving_mode:'automatic',autonomy:{ready:true,running:false}});
  assert.equal(c.canStartAutonomy(),true);
  c.emit({state:'driving',autonomy:{ready:true,running:true,state:'right',reason:'inner_right'}});
  assert.equal(c.canStartAutonomy(),false);assert.equal(c.autoSession,null);
  c.emit({state:'stopped',autonomy:{ready:true,running:false,state:'lost_line',reason:'line_lost'}});
  assert.equal(c.canStartAutonomy(),true);assert.equal(c.autoSession,null);
});
for(const direction of ['left','right']){
  test(`visible ${direction} inner immediately shows correction without a confirmation phase`,()=>{
    const state={state:'driving',autonomy:{running:true,state:direction,reason:'inner_'+direction,line_gap_active:false}};
    assert.match(autonomyLabel(state,true),/内侧偏线.*纠偏/);
    assert.doesNotMatch(autonomyLabel(state,true),/确认|完成|准备就绪/);
  });
}
test('bounded gap label is observational and preserves motor-fault priority',()=>{
  const state={state:'driving',autonomy:{running:true,line_gap_active:true}};
  assert.match(autonomyLabel(state,true),/位置暂不可辨.*保持上一动作/);
  assert.match(autonomyLabel(state,false),/其他页面控制中/);
  assert.match(autonomyLabel({...state,state:'fault'},true),/电机异常/);
});
test('v6 banner has continuous feedback, measured channels and unchanged start84',()=>{
  const source=require('node:fs').readFileSync(require.resolve('../control.js'),'utf8');
  assert.match(source,/连续循线v6/);assert.match(source,/车身左→右为X4→X1/);
  assert.doesNotMatch(source,/X1→X4 从左到右|直角|停转确认|寻找中线/);
  assert.match(source,/autonomy_start',speed:84/);assert.match(source,/直行84/);
  assert.match(source,/外侧纠偏100/);assert.match(source,/居中即前进/);
});
