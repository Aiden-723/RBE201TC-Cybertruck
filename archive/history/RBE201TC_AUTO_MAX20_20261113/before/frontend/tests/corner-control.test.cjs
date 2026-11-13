'use strict';
// Continuous UI fixtures only; no hardware requests or ownership creation.
const {test}=require('node:test');
const assert=require('node:assert/strict');
const {autonomyLabel,WheelControl}=require('../control.js');
for(const [direction,label] of [['left','左'],['right','右']]){
  test(`current ${direction} turn reports direction without estimating an angle`,()=>{
    const state={state:'driving',autonomy:{state:direction,reason:'hard_'+direction,running:true,ready:true}};
    assert.match(autonomyLabel(state,true),new RegExp('正在'+label+'转'));
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
test('automatic banner stays concise and retains start50',()=>{
  const source=require('node:fs').readFileSync(require.resolve('../control.js'),'utf8');
  assert.match(source,/autonomy_start',speed:50/);assert.match(source,/直行50/);
  assert.match(source,/连续差速纠偏/);
  assert.doesNotMatch(source,/居中即前进|每次动作先200|见线按官方组合规则/);
});

test('lost line reports stopped and never advertises blind search',()=>{
  const state={state:'stopped',outputs:[0,0,0,0],autonomy:{ready:true,running:false,state:'lost_line',reason:'line_lost'}};
  assert.match(autonomyLabel(state,true),/丢线停车/);
  assert.doesNotMatch(autonomyLabel(state,true),/原地找线|保持上一动作/);
  const source=require('node:fs').readFileSync(require.resolve('../control.js'),'utf8');
  assert.match(source,/line_gap_timeout/);assert.doesNotMatch(source,/line_gap_search_left|line_gap_search_right/);
});

test('cruise50 is not advertised as the absolute output ceiling or repeated assist',()=>{
  const source=require('node:fs').readFileSync(require.resolve('../control.js'),'utf8');
  assert.doesNotMatch(source,/每次动作先200\/0\.2秒/);assert.doesNotMatch(source,/自动上限50/);
});

test('short white gap labels continuation, with no inferred search direction',()=>{
  const state={state:'driving',autonomy:{running:true,line_gap_active:true,hold_pattern:'white',reason:'line_gap_hold'}};
  assert.match(autonomyLabel(state,true),/短暂丢线.*延续上一动作/);
  assert.doesNotMatch(autonomyLabel(state,true),/原地找线|向左|向右/);
});
