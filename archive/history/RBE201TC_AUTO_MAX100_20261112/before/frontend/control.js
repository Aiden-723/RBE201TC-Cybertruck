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
function autonomyLabel(state,owned=false) {
  if(['loading','offline'].includes(state.state))return '等待连接 · 请确认实物已停';
  if(state.state==='stopping')return '正在请求停车';
  if(state.state==='switching')return '切换模式中';
  if(state.state==='autonomy_starting')return '正在启动 · 等待握手';
  if(state.state==='fault')return '电机异常 · 请断开电源';
  const autonomy=state.autonomy||{};
  if(autonomy.running&&autonomy.line_gap_active)return '位置暂不可辨 · 保持上一动作'+(owned?'':' · 其他页面控制中');
  const labels={straight:'居中直行 · 继续前进',center:'居中直行 · 继续前进',forward:'直行',left:'左转纠偏',right:'右转纠偏',soft_left:'左转（微调）',soft_right:'右转（微调）',turn_left:'左转纠偏',turn_right:'右转纠偏',
    hard_left:'左外侧见线 · 强纠偏',hard_right:'右外侧见线 · 强纠偏',inner_left:'左内侧偏线 · 纠偏',inner_right:'右内侧偏线 · 纠偏',
    line_gap_timeout:'持续不可定位 · 已请求停车',line_loss_timeout:'持续不可定位 · 已请求停车',
    lost_line:'丢线停车',line_lost:'丢线停车',stale:'数据过期 · 停车',data_stale:'数据过期 · 停车',
    stale_data:'数据过期 · 停车',sensor_stale:'数据过期 · 停车',waiting:'等待循线数据',
    waiting_data:'等待循线数据',waiting_heartbeat:'已准备 · 等待本页心跳',heartbeat_expired:'心跳超时 · 已请求停车',
    ready:'准备就绪',stopped:'已停止',idle:'准备就绪',fault:'循线异常 · 请确认实物已停'};
  const label=labels[autonomy.reason]||labels[autonomy.state]||(autonomy.ready?'准备就绪':'等待连接 / 循线数据');
  return label+(autonomy.running&&!owned?' · 其他页面控制中':'');
}
class WheelControl {
  constructor({fetch=globalThis.fetch.bind(globalThis),change=()=>{},interval=100,autonomyInterval=200}={}) {
    this.fetch=fetch;this.change=change;this.interval=interval;this.autonomyInterval=autonomyInterval;
    this.token='';this.key='';this.session=null;this.seq=0;this.active=null;this.generation=0;this.sessionEpoch=0;this.arming=null;
    this.autoSession=null;this.autoSeq=0;this.autoStarting=null;
    this.state={enabled:false,armed:false,state:'loading',pwm:[0,0,0,0]};
  }
  emit(state) {
    // An observed GET or motor session must never become an autonomous ownership credential.
    const {autonomy_session:ignored,...observed}=state;
    this.state={...this.state,...observed,...(Object.hasOwn(observed,'outputs')?{received_at:performance.now()}: {})};this.change(this.state);
  }
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
    if(this.refreshing||this.active||this.arming||this.switching||this.autoStarting||this.autoSession||this.state.state==='stopping')return;
    this.refreshing=true;const generation=this.generation;
    try {
      const state=await this.request();
      if(generation!==this.generation)return;
      this.key=state.control_key;
      if(!state.armed&&this.session){this.session=null;this.cancelTimers();}
      this.emit(state);
    } catch(_) {
      if(generation===this.generation){
        // Observing another owner's state grants no right to stop it on this page's GET failure.
        if(this.session||this.autoSession||this.autoStarting||this.active||this.arming||this.switching)await this.stop('connection_lost');
        else this.emit({state:'offline',reason:'connection_lost'});
      }
    }
    finally {this.refreshing=false;}
  }
  cancelTimers() {clearTimeout(this.timer);this.timer=null;this.active=null;}
  cancelAutonomy() {clearTimeout(this.autoTimer);this.autoTimer=null;this.autoSession=null;this.autoSeq=0;this.autoStarting=null;}
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
  async setMode(mode) {
    if(this.switching||mode===this.state.driving_mode)return;
    this.switching=true;this.cancelTimers();this.cancelAutonomy();const generation=++this.generation;
    this.sessionEpoch++;this.session=null;this.emit({state:'switching',error:null});
    try {
      if(!this.key){const state=await this.request();this.key=state.control_key;}
      if(generation!==this.generation)return;
      const stopped=await this.request({action:'stop'});
      if(generation!==this.generation)return;
      this.emit({...stopped,state:'switching'});
      const state=await this.request({action:'set_mode',mode});
      if(generation===this.generation)this.emit({...state,error:null});
    } catch(error) {
      if(generation===this.generation){await this.stop('connection_lost');this.emit({error:error.message});}
    } finally {this.switching=false;this.emit({});}
  }
  async start(direction,speed=200) {
    if(this.switching||this.state.driving_mode==='automatic')return;
    this.activeSpeed=speed;
    if(this.active){this.active=direction;this.emit({});return;}
    this.active=direction;const generation=++this.generation;
    try {await this.arm();}
    catch(error){if(generation===this.generation){await this.stop('connection_lost');this.emit({error:error.message});}return;}
    if(generation!==this.generation||!this.active||!this.session)return;
    const tick=async()=>{
      if(generation!==this.generation||!this.active||!this.session)return;
      try {
        const state=await this.request({action:'drive',session:this.session,seq:++this.seq,direction:this.active,speed:this.activeSpeed});
        if(generation!==this.generation)return;
        this.emit(state);
        if(generation!==this.generation)return;
        if(!state.armed){this.session=null;this.cancelTimers();return;}
        this.timer=setTimeout(tick,this.interval);
      } catch(_) {if(generation===this.generation)await this.stop('connection_lost');}
    };
    return tick();
  }
  canStartAutonomy() {
    return this.state.driving_mode==='automatic'&&this.state.enabled&&!!this.state.autonomy?.ready&&!this.state.autonomy?.running&&
      !this.switching&&!this.autoSession&&!this.autoStarting&&!['loading','offline','fault','switching','stopping','autonomy_starting'].includes(this.state.state);
  }
  async startAutonomy() {
    if(!this.canStartAutonomy())return;
    this.cancelTimers();this.sessionEpoch++;this.session=null;
    const generation=++this.generation;
    this.emit({state:'autonomy_starting',error:null});
    const starting=(async()=>{
      try {
        // Starting only arms the controller. Only this response's opaque token may authorize heartbeat #1.
        const state=await this.request({action:'autonomy_start',speed:84});
        if(generation!==this.generation){await this.request({action:'stop'});return;}
        if(typeof state.autonomy_session!=='string'||!state.autonomy_session)throw Error('missing_autonomy_session');
        this.autoSession=state.autonomy_session;this.autoSeq=0;
        this.emit(state);
        await this.autonomyTick(generation);
      } catch(error) {
        if(generation===this.generation){await this.stop('connection_lost');this.emit({error:error.message});}
      }
    })();
    this.autoStarting=starting;
    try {await starting;}finally{if(this.autoStarting===starting){this.autoStarting=null;this.emit({});}}
  }
  async autonomyTick(generation) {
    if(generation!==this.generation||!this.autoSession)return;
    try {
      const state=await this.request({action:'autonomy_heartbeat',autonomy_session:this.autoSession,seq:++this.autoSeq});
      if(generation!==this.generation||!this.autoSession)return;
      this.emit(state);
      if(generation!==this.generation||!this.autoSession)return;
      if(!state.autonomy?.running){this.cancelAutonomy();this.emit({});return;}
      // Schedule only after the previous response; never overlap or retry an uncertain heartbeat.
      this.autoTimer=setTimeout(()=>this.autonomyTick(generation),this.autonomyInterval);
    } catch(_) {if(generation===this.generation)await this.stop('connection_lost');}
  }
  async release() {
    if(this.autoSession||this.autoStarting)return;
    const wasActive=!!this.active;
    this.cancelTimers();const generation=++this.generation;
    if(!this.session||!wasActive)return;
    try {
      const state=await this.request({action:'release',session:this.session,seq:++this.seq});
      if(generation===this.generation)this.emit(state);
    } catch(_) {if(generation===this.generation)await this.stop('connection_lost');}
  }
  async stop(reason='operator_stop') {
    this.cancelTimers();this.cancelAutonomy();const generation=++this.generation;this.sessionEpoch++;this.session=null;
    // Keep last confirmed outputs until STOP is acknowledged. A failed request is not physical stopping proof.
    this.emit({state:'stopping',reason,error:null});
    if(!this.key){this.emit({state:'offline',reason:'connection_lost'});return;}
    try {
      const state=await this.request({action:'stop'});
      if(generation===this.generation)this.emit({...state,reason});
    } catch(_) {if(generation===this.generation)this.emit({state:'offline',reason:'connection_lost'});}
  }
}
if(typeof module!=='undefined')module.exports={WheelControl,DRIVE_KEYS,directionForKeys,autonomyLabel};

function mountWheelControl() {
  const $=id=>document.getElementById(id), buttons=[...document.querySelectorAll('[data-drive]')];
  const heldKeys=new Set();let heldPointer=null;
  const clearHeld=()=>{heldKeys.clear();heldPointer=null;};
  const labels={loading:'正在连接',arming:'正在连接电机',disabled:'电机接口未开启',switching:'切换中',stopped:'就绪',armed:'就绪',
    driving:'控制中',stopping:'停止中',offline:'连接中断 · 请确认轮子已停',error:'启用失败',fault:'串口异常 · 请断开电机电源'};
  const reasons={heartbeat_expired:'控制信号超时，已发送停车指令并停用控制。',
    connection_lost:'连接异常。若轮子仍转，请立即断开电机电源。',blur:'页面失去焦点，已请求停车并停用控制。',
    operator_stop:'已发送停车指令，再按方向键即可继续。',key_released:'已松开，停车。按住方向键可继续。',
    idle_expired:'按住方向键即可控制。',startup_stop:'直接按住方向键或 WASD，也可按住屏幕按钮。'};
  const control=new WheelControl({change:state=>{
    document.dispatchEvent(new CustomEvent('rdk-motion', {detail:{received_at:state.received_at,state:state.state, enabled:state.enabled, armed:state.armed, fault:state.fault, outputs:state.outputs||state.pwm, last_drive_age_seconds:state.last_drive_age_seconds, lease_ms:state.lease_ms}}));
    if(['switching','stopping','offline','error','fault'].includes(state.state))clearHeld();
    const automatic=state.driving_mode==='automatic';
    if(automatic)clearHeld();
    const modeAvailable=!control.switching&&!['loading','offline','switching','stopping'].includes(state.state);
    $('mode-manual').disabled=$('mode-auto').disabled=!modeAvailable;
    $('mode-manual').setAttribute('aria-pressed',String(!automatic));
    $('mode-auto').setAttribute('aria-pressed',String(automatic));
    $('manual-controls').hidden=automatic;
    $('auto-controls').hidden=!automatic;
    $('camera-title').textContent=automatic?'基础循线实时画面':'驾驶实时画面';
    const autoStatus=autonomyLabel(state,!!control.autoSession);
    $('autonomy-status').textContent=autoStatus;
    $('auto-start').disabled=!control.canStartAutonomy();
    $('drive-speed').disabled=automatic||control.switching;
    const owned=state.armed&&!!control.session;
    $('drive-status').textContent=automatic?autoStatus:state.armed&&!owned?'其他端控制中':labels[state.state]||'等待控制';
    $('drive-status').className='sensor-badge '+(state.state==='driving'?'live':state.state==='fault'?'error':'');
    const available=!automatic&&!control.switching&&state.enabled&&!['fault','offline','loading','stopping','switching'].includes(state.state);
    buttons.forEach(button=>{button.disabled=!available;button.classList.toggle('held',!!control.active?.split('_').includes(button.dataset.drive));});
    $('drive-message').textContent=state.error==='already_in_use'?'其他页面正在控制，点一下急停即可接管。':state.error?'控制失败：'+state.error:
      reasons[state.reason]||(state.state==='armed'?'按住多久就转多久，松开停车。':'按键发出的指令不能证明实际轮速，请看实物。');
    $('drive-message').hidden=!state.error&&!['offline','fault','error'].includes(state.state);
    const autoMessage=$('auto-message')||$('drive-message');
    if($('auto-message'))autoMessage.hidden=!automatic;
    if(automatic){
      autoMessage.hidden=false;
      autoMessage.textContent='基础循线（非完整自主房间任务）·连续循线v6·输出修正·直行84·外侧纠偏200。已实测：车身左→右为X4→X1；黑=0、地板=1；见线即纠偏，居中即前进；不设转角目标。'+
        (state.error?'控制失败：'+state.error+'。':'')+autoStatus+'。'+
        (state.reason==='connection_lost'?'连接异常；不能确认实物已停，请检查或断开电机电源。':
          state.autonomy?.running&&!control.autoSession?'本页仅观测，不接管心跳。':
          control.autoSession?'本页持续心跳；切换模式、离开页面或空格 / Esc 会请求停车。':'仅点击“开始自动驾驶”才会启动。');
    }
    $('drive-pwm').textContent=(state.outputs||state.pwm||[0,0,0,0]).map((v,i)=>'M'+(i+1)+' '+(v>0?'+':'')+v).join('   ·   ');
  }});
  $('mode-manual').addEventListener('click',()=>{clearHeld();control.setMode('manual');});
  $('mode-auto').addEventListener('click',()=>{clearHeld();control.setMode('automatic');});
  $('auto-start').addEventListener('click',()=>{if(!document.hidden){clearHeld();control.startAutonomy();}});
  $('auto-stop').addEventListener('click',()=>{clearHeld();control.stop();});
  $('drive-stop').addEventListener('click',()=>{clearHeld();control.stop();});
  $('drive-speed').addEventListener('input',()=>{clearHeld();control.release();$('drive-speed-value').textContent=$('drive-speed').value;});
  const speed=()=>Number($('drive-speed').value);
  const updateKeys=()=>{const direction=directionForKeys(heldKeys);if(direction)control.start(direction,speed());else control.release();};
  for(const button of buttons){
    button.addEventListener('pointerdown',e=>{
      if(button.disabled||control.switching||control.state.driving_mode==='automatic'||e.button!==0||heldPointer!==null||heldKeys.size)return;
      e.preventDefault();heldPointer=e.pointerId;button.setPointerCapture(e.pointerId);control.start(button.dataset.drive,speed());
    });
    const release=e=>{if(heldPointer===e.pointerId){heldPointer=null;control.release();}};
    button.addEventListener('pointerup',release);button.addEventListener('pointercancel',release);button.addEventListener('lostpointercapture',release);
    button.addEventListener('contextmenu',e=>e.preventDefault());
  }
  window.addEventListener('keydown',e=>{
    if(e.code==='Space'||e.code==='Escape'){
      if(control.state.enabled||control.session||control.active||control.arming||control.autoSession||control.autoStarting||control.switching){
        e.preventDefault();clearHeld();control.stop();
      }
      return;
    }
    if(e.ctrlKey||e.metaKey||e.altKey||e.isComposing||e.target.closest?.('input:not([type="range"]),textarea,select,[contenteditable="true"]'))return;
    if(control.switching||control.state.driving_mode==='automatic'||!DRIVE_KEYS[e.code]||!control.state.enabled||['fault','offline','loading'].includes(control.state.state))return;
    e.preventDefault();if(e.repeat||heldKeys.has(e.code)||heldPointer!==null)return;
    heldKeys.add(e.code);updateKeys();
  });
  window.addEventListener('keyup',e=>{if(heldKeys.delete(e.code)){e.preventDefault();updateKeys();}});
  const loseFocus=()=>{clearHeld();if(control.session||control.active||control.arming||control.autoSession||control.autoStarting||control.switching)control.stop('blur');};
  window.addEventListener('blur',loseFocus);
  document.addEventListener('visibilitychange',()=>{if(document.hidden)loseFocus();});
  document.addEventListener('rdk-token',e=>{loseFocus();control.token=e.detail;control.refresh();});
  const timer=setInterval(()=>{if(!document.hidden)control.refresh();},250);
  window.addEventListener('pagehide',()=>{clearInterval(timer);loseFocus();});
  control.refresh();
}
if(typeof document!=='undefined')mountWheelControl();
