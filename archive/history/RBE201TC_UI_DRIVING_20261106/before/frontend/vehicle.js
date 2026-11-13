'use strict';
// Presentation only: no fetch, motor requests, or hardware access.
function motionView(state, elapsed=0, mapping=[null,null]) {
  const raw=state?.outputs;
  const valid=Array.isArray(raw)&&raw.length===4&&raw.every(v=>Number.isFinite(v)&&Math.abs(v)<=1000);
  const age=state?.last_drive_age_seconds;
  const lease=Number.isFinite(state?.lease_ms)?Math.min(1500,state.lease_ms):450;
  const fresh=elapsed>=0&&elapsed<1500&&(state?.state!=='driving'||(Number.isFinite(age)&&age>=0&&age*1000+elapsed<lease));
  const online=valid&&state?.enabled&&!state?.fault&&fresh&&['driving','armed','stopped'].includes(state.state);
  const outputs=online?raw:[0,0,0,0];
  const active=online&&state.armed&&state.state==='driving';
  const wheels=[0,0,0,0];let ambiguous=false;
  for(let side=0;side<2;side++){
    const base=side*2, front=mapping[side];
    if(front===base||front===base+1){wheels[base]=outputs[front];wheels[base+1]=outputs[base+(front===base?1:0)];}
    else if(outputs[base]===outputs[base+1])wheels[base]=wheels[base+1]=outputs[base];
    else ambiguous=true;
  }
  if(!active)wheels.fill(0);
  let label='无转动指令';
  if(!online)label='数据不可用 · 动画已停';
  else if(active&&outputs.some(Boolean)){
    const l=(outputs[0]+outputs[1])/2,r=(outputs[2]+outputs[3])/2;
    if(ambiguous)label='逐轮指令 · 请设置前后轮位';
    else if(l===r)label=l>0?'前进':'后退';
    else if(l*r<0)label=l>r?'原地右转':'原地左转';
    else label=(l+r>=0?'前进':'后退')+' · '+(l>r?'右转':'左转');
  }
  return {outputs,wheels,online,ambiguous,label};
}
if(typeof module!=='undefined')module.exports={motionView};
if(typeof document!=='undefined' && document.getElementById('vehicle-photo'))mountVehiclePhoto();
function mountVehiclePhoto(){
  const $=id=>document.getElementById(id), rotors=[$('wheel-front'),$('wheel-rear')];
  let state={},received=-Infinity,last=0,angles=[0,0],mapping=[null,null];
  for(const link of document.querySelectorAll('a[href="#advanced"]'))link.addEventListener('click',()=>{document.getElementById('advanced').open=true;});
  const reduced=window.matchMedia('(prefers-reduced-motion: reduce)');
  try{const saved=JSON.parse(localStorage.getItem('rdk-wheel-view-v1')||'null');if(Array.isArray(saved)&&saved.length===2)mapping=saved.map((n,i)=>[i*2,i*2+1].includes(n)?n:null);}catch(_){}
  function text(id,value){if($(id).textContent!==value)$(id).textContent=value;}
  function mapLabel(){text('wheel-map-summary',mapping.every(n=>n!==null)?'已设置 · 仅影响显示':'前后轮未设置');}mapLabel();
  ['map-left','map-right'].forEach((id,i)=>{
    $(id).value=mapping[i]===null?'':String(mapping[i]);
    $(id).addEventListener('change',()=>{mapping[i]=$(id).value===''?null:Number($(id).value);try{localStorage.setItem('rdk-wheel-view-v1',JSON.stringify(mapping));}catch(_){}mapLabel();});
  });
  document.addEventListener('rdk-motion',e=>{state=e.detail;received=Number.isFinite(state.received_at)?state.received_at:performance.now();});
  function frame(now){
    if(now-last<1000/30){requestAnimationFrame(frame);return;}
    const dt=Math.min((now-last)/1000,.05);last=now;
    if(document.hidden){requestAnimationFrame(frame);return;}
    const view=motionView(state,now-received,mapping);
    if(!reduced.matches)for(let i=0;i<2;i++)if(view.wheels[i]){
      // Near-side forward motion is counterclockwise in this left-facing image.
      angles[i]=(angles[i]-view.wheels[i]/200*dt*210)%360;
      rotors[i].setAttribute('transform','rotate('+angles[i].toFixed(3)+')');
    }
    text('vehicle-motion',view.label==='无转动指令'?'待命':!view.online?(state.state==='disabled'?'电机未连接':'连接中断'):view.ambiguous?'逐轮测试':view.label);
    const values=view.online?view.outputs.map(v=>(v>0?'+':'')+v):['—','—','—','—'];
    text('vehicle-left',values.slice(0,2).join(' / '));text('vehicle-right',values.slice(2).join(' / '));
    text('vehicle-feedback',view.ambiguous?'单轮位置未确认；数值正常显示，未指定的轮子不猜测。':'动画依据电机指令，不代表实测轮速。');
    requestAnimationFrame(frame);
  }
  requestAnimationFrame(frame);
}
