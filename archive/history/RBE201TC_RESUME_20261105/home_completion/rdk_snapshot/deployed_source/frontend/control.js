'use strict';
const DRIVE_KEYS = {ArrowUp:'forward',ArrowDown:'backward',ArrowLeft:'left',ArrowRight:'right',KeyW:'forward',KeyS:'backward',KeyA:'left',KeyD:'right'};
function directionForKeys(keys) {
  const directions=new Set([...keys].map(key=>DRIVE_KEYS[key]));
  const forward=Number(directions.has('forward'))-Number(directions.has('backward'));
  const right=Number(directions.has('right'))-Number(directions.has('left'));
  if(!forward&&!right)return null;
  const travel=forward>0?'forward':forward<0?'backward':null;
  const turn=right>0?'right':right<0?'left':null;
  return travel&&turn?travel+'_'+turn:travel||turn;
}
class WheelControl {
  constructor({fetch=globalThis.fetch.bind(globalThis),change=()=>{},interval=100}={}) {
    this.fetch=fetch;this.change=change;this.interval=interval;
    this.token='';this.key='';this.session=null;this.seq=0;this.active=null;this.generation=0;this.sessionEpoch=0;this.arming=null;
    this.state={enabled:false,armed:false,state:'loading',pwm:[0,0,0,0]};
  }
  emit(state) {this.state={...this.state,...state};this.change(this.state);}
  async request(data) {
    const abort=new AbortController(), timeout=setTimeout(()=>abort.abort(),800);
    try {
      const headers=this.token?{Authorization:'Bearer '+this.token}:{};
      if(data)Object.assign(headers,{'Content-Type':'application/json','X-Control-Key':this.key});
      const response=await this.fetch('/api/v1/control',{method:data?'POST':'GET',headers,credentials:'omit',
        cache:'no-store',redirect:'error',signal:abort.signal,keepalive:!!data,
        ...(data?{body:JSON.stringify(data)}:{})});
      const payload=await response.json();
      if(!response.ok)throw Error(payload.error?.code||'control_request_failed');
      return payload.data;
    } finally {clearTimeout(timeout);}
  }
  async refresh() {
    if(this.refreshing||this.active||this.arming)return;
    this.refreshing=true;const generation=this.generation;
    try {
      const state=await this.request();
      this.key=state.control_key;
      if(generation!==this.generation)return;
      if(!state.armed&&this.session){this.session=null;this.cancelTimers();}
      this.emit(state);
    } catch(_) {if(generation===this.generation){this.session=null;this.cancelTimers();this.emit({armed:false,state:'offline'});}}
    finally {this.refreshing=false;}
  }
  cancelTimers() {clearTimeout(this.timer);this.active=null;}
  async arm() {
    if(this.session)return;
    if(this.arming)return this.arming;
    const epoch=this.sessionEpoch;
    this.emit({state:'arming',error:null});
    this.arming=(async()=>{
      if(!this.key){const state=await this.request();this.key=state.control_key;}
      if(epoch!==this.sessionEpoch)return;
      const state=await this.request({action:'arm'});
      if(epoch!==this.sessionEpoch){await this.request({action:'stop'});return;}
      this.session=state.session;this.seq=0;this.emit(state);
    })();
    try {await this.arming;}finally{this.arming=null;}
  }
  async start(direction,speed) {
    this.activeSpeed=speed;
    if(this.active){this.active=direction;this.emit({});return;}
    this.active=direction;const generation=++this.generation;
    try {await this.arm();}
    catch(error){if(generation===this.generation){this.cancelTimers();this.emit({armed:false,state:'error',error:error.message});}return;}
    if(generation!==this.generation||!this.active||!this.session)return;
    const tick=async()=>{
      if(generation!==this.generation||!this.active||!this.session)return;
      try {
        const state=await this.request({action:'drive',session:this.session,seq:++this.seq,direction:this.active,speed:this.activeSpeed});
        if(generation!==this.generation)return;
        this.emit(state);
        if(!state.armed){this.session=null;this.cancelTimers();return;}
        this.timer=setTimeout(tick,this.interval);
      } catch(_) {if(generation===this.generation)this.stop('connection_lost');}
    };
    return tick();
  }
  async release() {
    const wasActive=!!this.active;
    this.cancelTimers();const generation=++this.generation;
    if(!this.session||!wasActive)return;
    try {
      const state=await this.request({action:'release',session:this.session,seq:++this.seq});
      if(generation===this.generation)this.emit(state);
    } catch(_) {if(generation===this.generation)this.stop('connection_lost');}
  }
  async stop(reason='operator_stop') {
    this.cancelTimers();const generation=++this.generation;this.sessionEpoch++;this.session=null;
    this.emit({armed:false,state:'stopping',outputs:[0,0,0,0],pwm:[0,0,0,0],reason});
    if(!this.key){this.emit({state:'offline'});return;}
    try {
      const state=await this.request({action:'stop'});
      if(generation===this.generation)this.emit({...state,reason});
    } catch(_) {if(generation===this.generation)this.emit({state:'offline',reason:'connection_lost'});}
  }
}
if(typeof module!=='undefined')module.exports={WheelControl,DRIVE_KEYS,directionForKeys};

function mountWheelControl() {
  const $=id=>document.getElementById(id), buttons=[...document.querySelectorAll('[data-drive]')];
  const heldKeys=new Set();let heldPointer=null;
  const clearHeld=()=>{heldKeys.clear();heldPointer=null;};
  const labels={loading:'正在连接',arming:'正在连接电机',disabled:'电机接口未开启',stopped:'键盘控制就绪',armed:'键盘控制就绪',
    driving:'正在发送转动指令',stopping:'正在发送停止指令',offline:'连接中断 · 请确认轮子已停',error:'启用失败',fault:'串口异常 · 请断开电机电源'};
  const reasons={heartbeat_expired:'控制信号超时，已发送停车指令并停用控制。',
    connection_lost:'连接异常。若轮子仍转，请立即断开电机电源。',blur:'页面失去焦点，已请求停车并停用控制。',
    operator_stop:'已发送停车指令，再按方向键即可继续。',key_released:'已松开，停车。按住方向键可继续。',
    idle_expired:'按住方向键即可控制。',startup_stop:'直接按住方向键或 WASD，也可按住屏幕按钮。'};
  const control=new WheelControl({change:state=>{
    if(['stopping','offline','error','fault'].includes(state.state))clearHeld();
    const owned=state.armed&&!!control.session;
    $('drive-status').textContent=state.armed&&!owned?'其他页面控制中 · 急停可接管':labels[state.state]||'等待控制';
    $('drive-status').className='sensor-badge '+(state.state==='driving'?'live':state.state==='fault'?'error':'');
    const available=state.enabled&&!['fault','offline','loading','stopping'].includes(state.state);
    buttons.forEach(button=>{button.disabled=!available;button.classList.toggle('held',!!control.active?.split('_').includes(button.dataset.drive));});
    $('drive-message').textContent=state.error==='already_in_use'?'其他页面正在控制，点一下急停即可接管。':state.error?'控制失败：'+state.error:
      reasons[state.reason]||(state.state==='armed'?'按住多久就转多久，松开停车。':'按键发出的指令不能证明实际轮速，请看实物。');
    $('drive-pwm').textContent=(state.outputs||state.pwm||[0,0,0,0]).map((v,i)=>'M'+(i+1)+' '+(v>0?'+':'')+v).join('   ·   ');
  }});
  $('drive-stop').addEventListener('click',()=>{clearHeld();control.stop();});
  $('drive-speed').addEventListener('input',()=>{clearHeld();control.release();$('drive-speed-value').textContent=$('drive-speed').value;});
  const speed=()=>Number($('drive-speed').value);
  const updateKeys=()=>{const direction=directionForKeys(heldKeys);if(direction)control.start(direction,speed());else control.release();};
  for(const button of buttons){
    button.addEventListener('pointerdown',e=>{
      if(e.button!==0||heldPointer!==null||heldKeys.size)return;
      e.preventDefault();heldPointer=e.pointerId;button.setPointerCapture(e.pointerId);control.start(button.dataset.drive,speed());
    });
    const release=e=>{if(heldPointer===e.pointerId){heldPointer=null;control.release();}};
    button.addEventListener('pointerup',release);button.addEventListener('pointercancel',release);button.addEventListener('lostpointercapture',release);
    button.addEventListener('contextmenu',e=>e.preventDefault());
  }
  window.addEventListener('keydown',e=>{
    if(e.code==='Space'||e.code==='Escape'){
      if(control.state.enabled){e.preventDefault();clearHeld();control.stop();}return;
    }
    if(e.ctrlKey||e.metaKey||e.altKey||e.isComposing||e.target.closest?.('input:not([type="range"]),textarea,select,[contenteditable="true"]'))return;
    if(!DRIVE_KEYS[e.code]||!control.state.enabled||['fault','offline','loading'].includes(control.state.state))return;
    e.preventDefault();if(e.repeat||heldKeys.has(e.code)||heldPointer!==null)return;
    heldKeys.add(e.code);updateKeys();
  });
  window.addEventListener('keyup',e=>{if(heldKeys.delete(e.code)){e.preventDefault();updateKeys();}});
  const loseFocus=()=>{clearHeld();if(control.session||control.active)control.stop('blur');};
  window.addEventListener('blur',loseFocus);
  document.addEventListener('visibilitychange',()=>{if(document.hidden)loseFocus();});
  document.addEventListener('rdk-token',e=>{loseFocus();control.token=e.detail;control.refresh();});
  const timer=setInterval(()=>{if(!document.hidden)control.refresh();},1000);
  window.addEventListener('pagehide',()=>{clearInterval(timer);loseFocus();});
  control.refresh();
}
if(typeof document!=='undefined')mountWheelControl();
