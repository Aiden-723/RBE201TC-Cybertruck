'use strict';
function bytes(value) {
  if (!Number.isFinite(value) || value < 0) return '—';
  if (value < 1024) return `${value} B`;
  const unit = Math.min(4, Math.floor(Math.log(value) / Math.log(1024)));
  return `${(value / 1024 ** unit).toFixed(1)} ${['B', 'KiB', 'MiB', 'GiB', 'TiB'][unit]}`;
}
function percent(used, total) {
  return Number.isFinite(used) && Number.isFinite(total) && total > 0 && used >= 0 && used <= total ? used / total * 100 : null;
}
function encoderState(telemetry = {}, elapsed = 0) {
  const labels = {disabled:'被动串口未启用', not_sampled:'等待原始帧', disconnected:'串口断开', stale:'数据过期'};
  const stale = telemetry.status === 'observed' && (!Number.isFinite(telemetry.age_seconds) || telemetry.age_seconds + elapsed > (telemetry.stale_after_seconds ?? 2));
  return `${stale ? '数据过期' : labels[telemetry.status] || (telemetry.status === 'observed' ? '收到原始帧' : '尚无遥测')} · 编码器未验证`;
}
class Poller {
  constructor({fetch = globalThis.fetch.bind(globalThis), change = () => {}, interval = 2000, timeout = 5000} = {}) {
    this.fetch = fetch; this.change = change; this.interval = interval; this.timeout = timeout;
    this.token = ''; this.paused = false; this.busy = false; this.generation = 0;
    this.state = {phase:'waiting', data:null, received:0, message:'等待首次读取；暂无测量数据'};
  }
  emit() { this.change(this.state); }
  pause() {
    this.paused = true; this.pending = false; this.generation++;
    clearTimeout(this.timer); this.controller?.abort();
    this.state.phase = 'paused'; this.state.message = '自动刷新已暂停；显示最后快照，不代表当前状态'; this.emit();
  }
  resume() { this.paused = false; if (this.busy) this.pending = true; else return this.refresh(); }
  setToken(token) {
    this.token = token; this.generation++; this.controller?.abort();
    if (this.busy) this.pending = true; else return this.refresh();
  }
  async refresh() {
    if (this.busy) return;
    clearTimeout(this.timer); this.busy = true;
    const generation = this.generation;
    const controller = this.controller = new AbortController();
    let timedOut = false;
    const deadline = setTimeout(() => { timedOut = true; controller.abort(); }, this.timeout);
    this.emit();
    try {
      const response = await this.fetch('/api/v1/status', {method:'GET', cache:'no-store', credentials:'omit', redirect:'error', signal:controller.signal,
        headers: this.token ? {Authorization:`Bearer ${this.token}`} : {}});
      if (generation !== this.generation) return;
      if (response.status === 401) {
        this.state.phase = 'auth'; this.state.message = '需要访问令牌：打开「连接设置」输入令牌后重试。'; return;
      }
      if (!response.ok) throw Error('http');
      if (!response.headers.get('Content-Type')?.includes('application/json')) throw Error('format');
      const payload = await response.json();
      if (generation !== this.generation) return;
      if (payload.schema_version !== '1.0' || !Number.isFinite(Date.parse(payload.generated_at)) ||
          !['health','system','devices','telemetry'].every(key => payload.data?.[key] && typeof payload.data[key] === 'object' && !Array.isArray(payload.data[key]))) throw Error('schema');
      this.state.data = payload; this.state.received = performance.now();
      this.state.phase = this.paused ? 'paused' : 'live';
      this.state.message = this.paused ? '单次刷新完成；自动刷新仍暂停' : '传感器监控 · 自动读取真实状态';
    } catch (_) {
      if (generation === this.generation) {
        this.state.phase = 'error'; this.state.message = timedOut ? '请求超时；保留的快照已过期，请检查服务连接。' : '读取失败或响应格式不兼容；保留的快照已过期。';
      }
    } finally {
      clearTimeout(deadline); this.busy = false; this.emit();
      if (this.pending) { this.pending = false; this.refresh(); }
      else if (!this.paused && this.state.phase !== 'auth') this.timer = setTimeout(() => this.refresh(), this.interval);
    }
  }
}
function thermalSummary(zones) {
  const values = Array.isArray(zones) ? zones.filter(zone => Number.isFinite(zone?.celsius)).map(zone => zone.celsius) : [];
  return values.length ? `${values.length} 个区域 · ${Math.min(...values).toFixed(1)}–${Math.max(...values).toFixed(1)} °C` : '—';
}
if (typeof module !== 'undefined') module.exports = {bytes, percent, encoderState, Poller, thermalSummary};


function sensorLive(sensor, elapsed = 0) {
  return sensor?.status === 'live' && Number.isFinite(sensor.age_seconds) && sensor.age_seconds + elapsed <= (sensor.name === 'dht11' ? 9 : sensor.name === 'camera' ? 3 : 2);
}
function dhtDisplay(sensor = {}, elapsed = 0, {active = true, phase = 'live'} = {}) {
  const valid = value => Number.isFinite(value?.temperature_c) && Number.isFinite(value?.humidity_pct);
  const age = Number.isFinite(sensor.age_seconds) && sensor.age_seconds >= 0 && Number.isFinite(elapsed) && elapsed >= 0 ? sensor.age_seconds + elapsed : Infinity;
  const last = sensor.last_good;
  const lastAge = Number.isFinite(last?.monotonic) && Number.isFinite(sensor.monotonic) && sensor.monotonic >= last.monotonic ? sensor.monotonic - last.monotonic + age : Infinity;
  const current = sensor.status === 'live' && valid(sensor.value) && age <= 9;
  const cached = sensor.status === 'error' && valid(last?.value) && lastAge <= 9;
  const available = current || cached;
  const shown = current ? sensor.value : cached ? last.value : null;
  const shownAge = current ? age : lastAge;
  const label = !active ? (phase === 'paused' ? '已暂停' : '旧读数') : available ? '持续采样' :
    valid(last?.value) || sensor.status === 'live' ? '读数已过期' : sensor.status === 'error' ? '读取异常' : '等待采样';
  const note = available ? (active ? 'DHT11 · ' : '旧读数 · ') + Math.floor(shownAge) + '秒前更新' :
    valid(last?.value) ? '上次有效 ' + last.value.temperature_c + '°C / ' + last.value.humidity_pct + '%RH' : '尚无有效读数';
  const title = cached ? '本轮读取未成功，当前显示' + Math.floor(lastAge) + '秒前的有效读数。' + (sensor.error || '') : sensor.error || '';
  return {value: shown, available, cached, age: shownAge, label, note, title,
    tone: active && available ? 'live' : active && sensor.status === 'error' && !valid(last?.value) ? 'error' : 'stale'};
}
function sparklinePath(history, field) {
  const values = (Array.isArray(history) ? history : []).slice(-40).map(p => p[field]).filter(Number.isFinite);
  if (values.length < 2) return '';
  const min = Math.min(...values), range = Math.max(.5, Math.max(...values) - min);
  return values.map((v, i) => (i ? 'L' : 'M') + (i * 116 / (values.length - 1) + 2).toFixed(1) + ',' + (28 - (v - min) / range * 24).toFixed(1)).join(' ');
}
function frameDelay(deadline, now) { return Math.max(0, deadline - now); }
class FramePoller {
  constructor({fetch = globalThis.fetch.bind(globalThis), token = () => '', frame = () => {}, error = () => {}, interval = 1000 / 15} = {}) {
    this.fetch=fetch; this.token=token; this.frame=frame; this.error=error; this.interval=interval;
    this.enabled=false; this.busy=false; this.generation=0; this.lastFrameId=null; this.nextFrameAt=null;
  }
  setEnabled(enabled) {
    if (this.enabled === enabled) return;
    this.enabled=enabled; this.generation++; this.nextFrameAt=null;
    clearTimeout(this.timer);
    if (!enabled) this.controller?.abort();
    else this.refresh();
  }
  async refresh() {
    if (!this.enabled || this.busy) return;
    clearTimeout(this.timer);
    this.busy=true;
    const started=performance.now();
    // Anchor to the previous deadline so small timer delays do not accumulate.
    // When the link is too slow, drop the missed slot instead of overlapping requests.
    this.nextFrameAt=Math.max(this.nextFrameAt??started,started-this.interval)+this.interval;
    const generation=this.generation;
    const controller=this.controller=new AbortController();
    const timer=setTimeout(()=>controller.abort(), 2500);
    try {
      const response=await this.fetch('/api/v1/camera/frame', {cache:'no-store',credentials:'omit',redirect:'error',signal:controller.signal,headers:this.token()?{Authorization:'Bearer '+this.token()}:{}});
      if (!response.ok || !response.headers.get('Content-Type')?.includes('image/jpeg')) throw Error('frame');
      const blob=await response.blob();
      if (blob.size > 1048576 || blob.size < 4) throw Error('size');
      const frameId=response.headers.get('X-Frame-Id');
      if (this.enabled && generation===this.generation && (!frameId || frameId!==this.lastFrameId)) {
        this.lastFrameId=frameId; this.frame(blob,frameId);
      }
    } catch (_) {
      if (this.enabled && generation===this.generation) this.error();
    } finally {
      clearTimeout(timer); this.busy=false;
      if (this.enabled) this.timer=setTimeout(()=>this.refresh(),frameDelay(this.nextFrameAt??performance.now(),performance.now()));
    }
  }
}
if (typeof module !== 'undefined') Object.assign(module.exports,{sensorLive,dhtDisplay,sparklinePath,FramePoller,frameDelay});

function mount() {
  const $=id=>document.getElementById(id);
  const element=(tag,text,className)=>{
    const node=document.createElement(tag);
    if(text!==undefined) node.textContent=text;
    if(className) node.className=className;
    return node;
  };
  const value=item=>item?.status==='ok'?item.value:null;
  const stateLabel={disabled:'未启动',starting:'正在连接',error:'读取异常',stale:'数据过期',live:'实时采样'};
  let rendered=null, frameURL=null, lastFrameTime=null, imageAvailable=false;
  const displayedFrames=[];
  const probes=[0,1,2,3].map(i=>{
    const node=element('div',undefined,'probe unknown');
    node.append(element('span','X'+(i+1)),element('div','—','probe-lamp'),element('small','等待读取'));
    return node;
  });
  $('line-sensors').replaceChildren(...probes);
  const framePoll=new FramePoller({
    token:()=>poll.token,
    frame:blob=>{
      const next=URL.createObjectURL(blob), previous=frameURL;
      frameURL=next;
      $('camera-image').onload=()=>{
        if(frameURL!==next) return;
        lastFrameTime=performance.now(); imageAvailable=true;
        displayedFrames.push(lastFrameTime);
        while(displayedFrames.length && lastFrameTime-displayedFrames[0]>3000) displayedFrames.shift();
        $('camera-image').hidden=false; $('camera-placeholder').hidden=true;
      };
      $('camera-image').onerror=()=>{imageAvailable=false;};
      $('camera-image').src=next;
      if(previous) URL.revokeObjectURL(previous);
    }
  });
  function resource(label,text,note,ratio) {
    const node=element('div',undefined,'resource'), top=element('div',undefined,'resource-top');
    top.append(element('span',label),element('span',text,'resource-value'));node.append(top);
    if(Number.isFinite(ratio)){const bar=element('progress');bar.max=100;bar.value=ratio;bar.setAttribute('aria-label',label+'使用率 '+ratio.toFixed(1)+'%');node.append(bar);}
    node.append(element('p',note,'resource-note'));return node;
  }
  function renderData(payload) {
    const {system,devices,telemetry,host={}}=payload.data;
    const memory=value(system.memory_bytes), disk=value(system.disk_bytes), thermal=value(system.thermal), uptime=value(system.uptime_seconds);
    const used=memory?memory.total-memory.available:null, mr=percent(used,memory?.total), dr=percent(disk?.used,disk?.total);
    $('resources').replaceChildren(
      resource('内存',mr===null?'—':mr.toFixed(0)+'%',mr===null?'不可用':bytes(used)+' / '+bytes(memory.total),mr),
      resource('存储',dr===null?'—':dr.toFixed(0)+'%',dr===null?'不可用':bytes(disk.used)+' / '+bytes(disk.total),dr),
      resource('板载温度',thermalSummary(thermal),'不是DHT11环境温度'),
      resource('运行时长',Number.isFinite(uptime)?Math.floor(uptime/3600)+'时 '+Math.floor(uptime%3600/60)+'分':'—','开发板启动以来')
    );
    $('host-kind').textContent=host.is_rdk?'RDK X5 · 已识别':host.hostname||'主机待确认';
    $('host-address').textContent=host.is_rdk?'RDK X5 已连接 · 三类传感器独立采样 · 方向键直接控制车轮':'相机、环境与地面信号，在这里一起查看。';
    $('clock-warning').hidden=host.clock_valid!==false;
    const rows=[];
    function row(name,config,status){const r=element('tr');[name,config,status].forEach(t=>r.append(element('td',t)));rows.push(r);}
    const names={serial:'串口',video:'视频节点'};
    for(const key of ['serial','video']){
      const items=value(devices[key]);
      if(!Array.isArray(items)||!items.length) row(names[key],'—',Array.isArray(items)?'未发现节点':'不可用');
      else for(const item of items)row(names[key],item.node,'已发现 · 功能待验证');
    }
    row('相机','MIPI CSI1','见上方实时画面');
    row('循线','物理针脚 15 / 18 / 16 / 11','实时电平，黑白极性待核对');
    row('温湿度','GPIO401 · 物理针脚37','DHT11独立采样');
    $('devices').replaceChildren(...rows);
    $('encoders').replaceChildren(...[0,1,2,3].map(i=>{
      const n=element('div');n.append(element('span','M'+(i+1)),element('strong',Number.isInteger(telemetry.raw_counts?.[i])?String(telemetry.raw_counts[i]):'—'));return n;
    }));
    $('raw').textContent=JSON.stringify(payload,null,2);
  }
  function renderSensors(payload,elapsed,active,phase){
    const sensors=payload.data.sensors||{};
    const line={...sensors.line_sensors,name:'line_sensors'}, dht={...sensors.dht11,name:'dht11'}, camera={...sensors.camera,name:'camera'};
    const fresh=s=>sensorLive(s,elapsed);
    const label=s=>!active?(phase==='paused'?'已暂停':'旧读数'):(fresh(s)?'实时采样':stateLabel[s.status]||'等待采样');
    function badge(id,s){$(id).textContent=label(s);$(id).className='sensor-badge '+(active&&fresh(s)?'live':s.status==='error'?'error':'stale');$(id).title=s.error||'';}
    badge('line-status',line);badge('camera-status',camera);
    const dhtView=dhtDisplay(dht,elapsed,{active,phase});
    for(const kind of ['temperature','humidity']){
      $(kind+'-status').textContent=dhtView.label;
      $(kind+'-status').className='sensor-badge '+dhtView.tone;
      $(kind+'-status').title=dhtView.title;
      $(kind+'-note').textContent=dhtView.note;
      $(kind+'-note').title=dhtView.title;
    }
    $('temperature-value').textContent=Number.isFinite(dhtView.value?.temperature_c)?dhtView.value.temperature_c.toFixed(1):'—';
    $('humidity-value').textContent=Number.isFinite(dhtView.value?.humidity_pct)?dhtView.value.humidity_pct.toFixed(0):'—';
    $('temperature-chart').querySelector('path').setAttribute('d',sparklinePath(dht.history,'temperature_c'));
    $('humidity-chart').querySelector('path').setAttribute('d',sparklinePath(dht.history,'humidity_pct'));
    for(let i=0;i<4;i++){
      const v=fresh(line)&&Array.isArray(line.value)?line.value[i]:null;
      probes[i].className='probe '+(v===1?'high':v===0?'low':'unknown');
      probes[i].querySelector('.probe-lamp').textContent=v===0||v===1?String(v):'—';
      probes[i].querySelector('small').textContent=v===1?'高电平':v===0?'低电平':'未读取';
    }
    $('line-message').textContent=line.error||(!active?'当前显示为最后一次快照。':'可用黑胶带或遮挡探头，观察四个信号的变化。');
    const canCamera=active&&fresh(camera);
    framePoll.setEnabled(canCamera);
    const frameAge=lastFrameTime===null?null:(performance.now()-lastFrameTime)/1000;
    const livePicture=canCamera&&imageAvailable&&frameAge!==null&&frameAge<3;
    $('camera-live').hidden=!livePicture;
    $('camera-overlay').hidden=!imageAvailable||livePicture;
    $('camera-overlay').textContent=phase==='paused'?'画面已暂停':!active?'连接中断 · 最后画面':'画面过期 · 等待更新';
    $('camera-placeholder').hidden=imageAvailable;
    $('camera-message').textContent=camera.error||'成功采集后，这里会显示真实画面。';
    $('camera-frame-age').textContent=frameAge===null?'等待首帧':Math.floor(frameAge)+'秒前更新';
    $('camera-interface').textContent=fresh(camera)?'CSI 1 · 真实画面':'CSI 1 · 等待采集';
    $('camera-resolution').textContent=camera.value?camera.value.width+' × '+camera.value.height:'— × —';
    const recentFrames=displayedFrames.filter(t=>performance.now()-t<3000);
    const displayedFps=livePicture&&recentFrames.length>1?(recentFrames.length-1)*1000/(recentFrames[recentFrames.length-1]-recentFrames[0]):null;
    $('camera-rate').textContent=Number.isFinite(displayedFps)?'显示 '+displayedFps.toFixed(1)+' 帧/秒':'显示 — 帧/秒';
    $('camera-rate').title='浏览器实际加载的不重复帧；目标15帧/秒。采集端 '+(camera.value?.fps??'—')+' 帧/秒。';
    let count=0;
    for(const [id,s] of [['check-camera',camera],['check-dht',dht],['check-line',line]]){
      const ok=active&&(id==='check-dht'?dhtView.available:fresh(s));if(ok)count++;
      $(id).textContent=ok?'已收到数据':id==='check-dht'?dhtView.label:label(s);$(id).className=ok?'ok':'warn';$(id).title=id==='check-dht'?dhtView.title:s.error||'';
    }
    $('sensor-count').textContent=count+' / 3';
  }
  function render(state) {
    const elapsed=state.data?Math.max(0,(performance.now()-state.received)/1000):0;
    const phase=state.phase==='live'&&elapsed>5?'stale':state.phase;
    const active=phase==='live';
    const labels={waiting:'正在连接',live:'设备已连接',auth:'需要令牌',error:'连接中断',paused:'更新已暂停',stale:'数据已过期'};
    $('connection').textContent=labels[phase]||'等待连接';$('connection').className='badge '+phase;
    $('notice').textContent=phase==='paused'?'更新已暂停，保留最后读数和画面。':state.message;
    $('notice').className=['live','waiting'].includes(phase)?'notice':'notice attention';
    $('refresh').disabled=poll.busy;
    $('pause').textContent=poll.paused?'恢复更新':'暂停更新';
    $('pause').setAttribute('aria-pressed',String(poll.paused));
    document.body.classList.toggle('snapshot-stale',!active);
    $('snapshot-label').textContent=active?'最近成功快照':'旧快照 · 非当前读数';
    if(!state.data){framePoll.setEnabled(false);return;}
    if(rendered!==state.data){renderData(state.data);rendered=state.data;}
    renderSensors(state.data,elapsed,active,phase);
    $('updated').textContent='每次响应后继续更新 · '+Math.floor(elapsed)+'秒前收到快照';
    const telemetry=state.data.data.telemetry;
    $('encoder-status').textContent=encoderState(telemetry,elapsed);
    $('encoder-age').textContent=Number.isFinite(telemetry.age_seconds)?'原始帧 '+(telemetry.age_seconds+elapsed).toFixed(1)+' 秒前':'串口未启用，不发送电机指令。';
  }
  const poll=new Poller({change:render,interval:700});
  $('refresh').addEventListener('click',()=>poll.refresh());
  $('pause').addEventListener('click',()=>poll.paused?poll.resume():poll.pause());
  $('settings').addEventListener('click',()=>{
    const open=$('settings-panel').hidden;
    $('settings-panel').hidden=!open;$('settings').setAttribute('aria-expanded',String(open));
    if(open)$('token').focus();
  });
  $('settings-panel').addEventListener('keydown',e=>{
    if(e.key==='Escape'){$('settings-panel').hidden=true;$('settings').setAttribute('aria-expanded','false');$('settings').focus();}
  });
  $('settings-form').addEventListener('submit',e=>{
    e.preventDefault();const token=$('token').value;$('token').value='';
    document.dispatchEvent(new CustomEvent('rdk-token',{detail:token}));
    framePoll.setEnabled(false);poll.setToken(token);
  });
  $('clear-token').addEventListener('click',()=>{$('token').value='';document.dispatchEvent(new CustomEvent('rdk-token',{detail:''}));framePoll.setEnabled(false);poll.setToken('');});
  const clock=setInterval(()=>render(poll.state),250);
  window.addEventListener('pagehide',()=>{clearInterval(clock);framePoll.setEnabled(false);poll.pause();poll.token='';if(frameURL)URL.revokeObjectURL(frameURL);});
  window.addEventListener('pageshow',e=>{if(e.persisted)location.reload();});
  poll.refresh();
}
if(typeof document!=='undefined')mount();
