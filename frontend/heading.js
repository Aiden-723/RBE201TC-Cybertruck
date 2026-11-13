'use strict';
function headingText(state) {
  if(state?.status!=='live')return '—';
  const angle=state.calibrated?state.heading_deg:state.yaw_deg;
  return Number.isFinite(angle)?(Math.abs(angle)<.05?0:angle).toFixed(1):'—';
}
if(typeof module!=='undefined')module.exports={headingText};
if(typeof document!=='undefined'){
  const value=document.getElementById('heading-value'),button=document.getElementById('heading-calibrate');
  let token='',moving=false,busy=false,reading=false,live=false,closed=false,generation=0;
  const update=()=>{button.disabled=!live||moving||busy;};
  async function request(path,data,key){
    const abort=new AbortController(),timer=setTimeout(()=>abort.abort(),800);
    try{
      const headers=token?{Authorization:'Bearer '+token}:{};
      if(data)Object.assign(headers,{'Content-Type':'application/json','X-Control-Key':key});
      const r=await fetch(path,{method:data?'POST':'GET',headers,credentials:'omit',cache:'no-store',redirect:'error',signal:abort.signal,...(data?{body:JSON.stringify(data)}:{})});
      const payload=await r.json();if(!r.ok)throw Error(payload.error?.code||'request_failed');return payload.data;
    }finally{clearTimeout(timer);}
  }
  async function poll(){
    if(closed||reading||busy||document.hidden)return;
    reading=true;const current=generation;
    try{const state=await request('/api/v1/imu');if(current!==generation)return;live=state.status==='live';value.textContent=headingText(state);}
    catch(_){if(current===generation){live=false;value.textContent='—';}}
    finally{reading=false;update();}
  }
  button.addEventListener('click',async()=>{
    if(button.disabled)return;
    busy=true;generation++;button.textContent='校准中';update();
    try{
      const state=await request('/api/v1/control');
      const calibrated=await request('/api/v1/control',{action:'imu_calibrate'},state.control_key);
      value.textContent=headingText(calibrated.imu);button.textContent='已校准';
      setTimeout(()=>{if(!busy)button.textContent='校准';},1200);
    }catch(_){button.textContent='请停止后重试';}
    finally{busy=false;update();}
  });
  document.addEventListener('rdk-motion',e=>{moving=!!e.detail.armed;update();});
  document.addEventListener('rdk-token',e=>{token=e.detail;generation++;poll();});
  const timer=setInterval(poll,50);
  window.addEventListener('pagehide',()=>{closed=true;clearInterval(timer);});
  poll();
}
