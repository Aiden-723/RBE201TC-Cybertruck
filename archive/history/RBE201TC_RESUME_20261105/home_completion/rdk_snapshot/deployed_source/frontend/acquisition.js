'use strict';
const ACQ_BASE='/api/v1/experiments';
const ACQ_STATUS={pending:'等待新结果',success:'有效',sensor_failure:'传感器失败',request_failure:'请求失败',timeout:'超时',cancelled:'已取消'};
const ACQ_ERRORS={sensor_only_bench_required:'当前是桌面传感器测试，暂不开放正式房间记录。',recorder_unavailable:'后台尚未配置持久记录目录，原电机控制不受影响。',run_already_open:'请先结束当前运行。',capture_in_progress:'当前还有一条记录正在等待。',formal_identifiers_required:'正式采集需要实际组号和房间标识。',measured_stations_required:'请提供实测站点，不能空表或猜测坐标。',bench_cannot_claim_room_coordinates:'无坐标联调不能携带房间坐标。',record_persistence_failed:'文件保存失败；不能把本次当作成功记录。',experiment_origin_rejected:'采集连接已变化，正在刷新；不会自动重发写入。',unauthorized:'访问令牌无效，请在原连接设置中更新。',unknown_station:'站点不在当前实测表中。',run_event_limit_reached:'本次运行达到记录上限，请结束并新建运行。'};

function parseStationCSV(source) {
  if(typeof source!=='string'||source.length>24000)throw Error('站点CSV需为24,000字符以内的文本。');
  source=source.replace(/^\uFEFF/,'');
  const rows=[];let row=[],field='',quoted=false,closed=false;
  const pushField=()=>{row.push(field);field='';closed=false;};
  const pushRow=()=>{pushField();if(row.some(value=>value.trim()!==''))rows.push(row);row=[];};
  for(let i=0;i<source.length;i++){
    const c=source[i];
    if(quoted){if(c==='"'){if(source[i+1]==='"'){field+='"';i++;}else{quoted=false;closed=true;}}else field+=c;continue;}
    if(c===','){pushField();continue;}
    if(c==='\n'||c==='\r'){if(c==='\r'&&source[i+1]==='\n')i++;pushRow();continue;}
    if(c==='"'){if(field!==''||closed)throw Error('CSV引号位置无效。');quoted=true;continue;}
    if(closed)throw Error('CSV闭引号之后有多余字符。');
    field+=c;
  }
  if(quoted)throw Error('CSV引号未闭合。');
  if(field!==''||row.length||closed)pushRow();
  if(rows.length<2)throw Error('站点表只有表头或为空；请填真实测量。');
  const headers=rows.shift().map(value=>value.trim());
  const required=['StationID','X_m','Y_m','SensorHeight_m'];
  if(new Set(headers).size!==headers.length||required.some(name=>!headers.includes(name)))throw Error('缺少StationID、X_m、Y_m或SensorHeight_m表头。');
  if(rows.length>100)throw Error('一次最多导入100个实际站点。');
  const used=new Set();
  const numeric=value=>{if(typeof value!=='string'||!/^[+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?$/.test(value.trim()))throw Error('坐标/高度为空或不是有效米制数字，不能自动补0。');const n=Number(value);if(!Number.isFinite(n))throw Error('坐标/高度必须有限。');return n;};
  return rows.map(values=>{
    if(values.length!==headers.length)throw Error('CSV行列数不一致。');
    const get=name=>values[headers.indexOf(name)];
    const id=get('StationID').trim(),height=numeric(get('SensorHeight_m'));
    if(!id||id.length>80||used.has(id)||height<0)throw Error('站点ID为空/重复/过长，或高度为负。');
    used.add(id);
    const notes=headers.includes('Notes')?get('Notes'):'';
    if(notes.length>1000)throw Error('单站点说明过长。');
    return {station_id:id,x_m:numeric(get('X_m')),y_m:numeric(get('Y_m')),height_m:height,notes};
  });
}

function acquisitionReading(row,key){
  const value=row[key];
  if(row.Status!=='success'||!['number','string'].includes(typeof value)||typeof value==='string'&&!value.trim())return '—';
  const n=Number(value);return Number.isFinite(n)?n.toFixed(1):'—';
}

class AcquisitionClient{
  constructor({fetch=globalThis.fetch.bind(globalThis),change=()=>{}}={}){
    this.fetch=fetch;this.change=change;this.token='';this.key='';this.viewRun=null;
    this.state={enabled:false,connected:false,runs:[],selected_run:null};
    this.refreshing=false;this.refreshQueued=false;this.selectionPending=false;
    this.commanding=false;this.epoch=0;this.mutationId=0;this.error=null;this.networkError=null;
    this.controllers=new Set();
  }
  headers(){return this.token?{Authorization:'Bearer '+this.token}:{};}
  async request(path='',data){
    const abort=new AbortController(),timer=setTimeout(()=>abort.abort(),4000);this.controllers.add(abort);
    try{
      const headers=this.headers();let body;
      if(data!==undefined){body=JSON.stringify(data);if(new TextEncoder().encode(body).length>32768)throw Error('采集配置超过32KiB，请精简说明或站点数量。');Object.assign(headers,{'Content-Type':'application/json','X-Experiment-Key':this.key});}
      const response=await this.fetch(ACQ_BASE+path,{method:data!==undefined?'POST':'GET',headers,body,credentials:'omit',cache:'no-store',redirect:'error',signal:abort.signal});
      const payload=await response.json();
      if(!response.ok)throw Error(payload.error?.code||'采集请求失败');
      if(!payload.data||typeof payload.data!=='object'||Array.isArray(payload.data))throw Error('采集响应格式无效');
      return payload.data;
    }finally{clearTimeout(timer);this.controllers.delete(abort);}
  }
  apply(data){if(data.experiment_key)this.key=data.experiment_key;this.networkError=null;this.state={...data,connected:true};this.change(this.state);}
  async refresh(){
    if(this.refreshing||this.commanding){this.refreshQueued=true;return;}
    this.refreshing=true;const epoch=this.epoch;
    try{
      const data=await this.request(this.viewRun?'/'+encodeURIComponent(this.viewRun):'');
      if(epoch===this.epoch){this.selectionPending=false;this.apply(data);}
    }catch(error){
      if(epoch===this.epoch){
        this.networkError=error.message;this.state={...this.state,connected:false};
        if(error.message==='experiment_not_found'){this.viewRun=null;++this.epoch;this.refreshQueued=true;}
      }
    }finally{
      this.refreshing=false;if(epoch===this.epoch)this.selectionPending=false;this.change(this.state);
      if(this.refreshQueued&&!this.commanding){this.refreshQueued=false;await this.refresh();}
    }
  }
  async select(runId){this.viewRun=runId||null;++this.epoch;this.selectionPending=true;this.change(this.state);await this.refresh();}
  setToken(token){
    token=typeof token==='string'?token:'';
    if(token===this.token)return this.refresh();
    ++this.epoch;++this.mutationId;this.token=token;this.key='';this.viewRun=null;
    this.error=this.networkError=null;this.commanding=false;this.selectionPending=true;
    this.state={enabled:false,connected:false,runs:[],selected_run:null};
    for(const controller of this.controllers)controller.abort();
    this.change(this.state);return this.refresh();
  }
  async command(data){
    if(this.commanding||this.selectionPending)throw Error('采集操作或视图正在更新，请勿重复点击。');
    if(!data||typeof data!=='object'||Array.isArray(data))throw Error('采集命令格式无效');
    this.commanding=true;const epoch=++this.epoch,id=++this.mutationId;
    this.error=null;this.change(this.state);
    try{
      if(!this.key){const state=await this.request();if(epoch!==this.epoch)return null;this.key=state.experiment_key||'';}
      const result=await this.request('',data);
      if(epoch!==this.epoch)return null;
      this.viewRun=data.action==='start_run'?null:result.selected_run?.run_id||null;
      this.apply(result);return result;
    }catch(error){if(epoch===this.epoch){this.error=error.message;throw error;}return null;}
    finally{if(id===this.mutationId){this.commanding=false;this.change(this.state);await this.refresh();}}
  }
  async download(kind){
    if(this.selectionPending||this.commanding||!this.state.connected)throw Error('视图尚未确认，请等待刷新后下载。');
    if(!['csv','manifest'].includes(kind)||!this.state.selected_run?.run_id)throw Error('先选择一个已有运行。');
    const id=this.state.selected_run.run_id,epoch=this.epoch;
    const abort=new AbortController(),timer=setTimeout(()=>abort.abort(),5000);this.controllers.add(abort);
    try{
      const response=await this.fetch(ACQ_BASE+'/'+encodeURIComponent(id)+'/'+kind,{headers:this.headers(),credentials:'omit',cache:'no-store',redirect:'error',signal:abort.signal});
      if(!response.ok)throw Error('下载失败，原始记录未更改。');
      const blob=await response.blob();if(epoch!==this.epoch)throw Error('访问或视图已变化，请重新下载快照。');
      return {blob,filename:kind==='csv'?'temperature_points_'+id+'.csv':'run_manifest_'+id+'.json'};
    }finally{clearTimeout(timer);this.controllers.delete(abort);}
  }
}

function mountAcquisition(){
  const $=id=>document.getElementById(id);if(!$('acquisition-panel'))return;
  function options(element,items,selected){
    const signature=JSON.stringify(items);
    if(element.dataset.signature!==signature){element.replaceChildren(...items.map(([value,label])=>{const item=document.createElement('option');item.value=value;item.textContent=label;return item;}));element.dataset.signature=signature;}
    if(items.some(([value])=>value===selected))element.value=selected;
  }
  const client=new AcquisitionClient({change:state=>{
    const available=state.connected&&state.enabled&&!client.selectionPending,run=client.selectionPending?null:state.selected_run,active=state.active_run_id;
    $('acq-connection').textContent=!state.connected?'采集连接中断':client.selectionPending?'正在确认所选运行':state.enabled?'记录接口已连接':'后台未配置记录目录';
    $('acq-connection').className='sensor-badge '+(available?'live':'');
    $('acq-config').disabled=!available||!!active||state.busy||client.commanding;
    const sensorOnly=state.sensor_only===true;
    $('acq-mode').textContent=sensorOnly?'桌面传感器测试 · 电机接口未开启；仅保存联调数据，不用于房间地图。':'站点记录 · 需保持实物停稳；正式数据必须关联实测坐标。';
    $('acq-purpose-formal').disabled=sensorOnly;
    if(sensorOnly&&$('acq-purpose').value==='room_stationary'){
      $('acq-purpose').value='bench_diagnostic';
      $('acq-formal').hidden=true;
      $('acq-start').textContent='新建联调运行';
    }
    $('acq-start').disabled=!available||!!active||state.busy||client.commanding;
    options($('acq-run'),state.runs?.map(r=>[r.run_id,(r.purpose==='bench_diagnostic'?'联调 · ':'正式 · ')+r.run_id])||[],client.selectionPending?client.viewRun:run?.run_id);
    $('acq-run').disabled=!state.connected||!state.enabled||!state.runs?.length||client.commanding;
    const stationValue=$('acq-station').value;
    options($('acq-station'),(state.stations||[]).map(id=>[id,id]),stationValue);
    const viewingActive=!!active&&run?.run_id===active;
    $('acq-station').disabled=!available||!viewingActive||state.busy||client.commanding;
    $('acq-capture').disabled=!available||!viewingActive||state.busy||state.closing||client.commanding;
    $('acq-cancel').disabled=!available||!state.busy||state.committing||state.closing||client.commanding;
    $('acq-close').disabled=!available||!active||state.closing||client.commanding;
    $('acq-csv').disabled=$('acq-manifest').disabled=!available||!run||client.commanding;
    $('acq-run-info').textContent=client.selectionPending?'正在载入所选运行；暂不允许记录或导出旧视图。':run?run.run_id+' · '+(run.purpose==='bench_diagnostic'?'无坐标联调（非正式房间数据）':'人工停稳正式站点')+' · '+(run.closed_at?'已结束':run.interrupted?'异常中断，保留原始pending':'运行中'):available?'尚无运行；不会自动采集。':'等待采集接口，原电机控制独立运行。';
    const message=client.error||client.networkError||state.error;
    $('acq-message').textContent=message?(message==='result_committing'?'结果已开始落盘，取消未被接受；可等待结果或结束运行。':ACQ_ERRORS[message]||message):client.selectionPending?'正在确认视图…':state.operation==='start'?'正在创建并保存新运行…':state.operation==='close'?'正在保存最终清单并结束运行…':state.closing?'正在完成本条收尾并结束运行…':state.committing?'结果正在落盘；终态提交开始后不能再取消。':state.busy?'等待下一次新发布结果，请保持实物停稳…':active?'已就绪：实物停稳并适应后，点击“记录下一条结果”。':'先新建运行；没有现场坐标时只做联调。';
    $('acq-message').classList.toggle('acq-error',!!message);
    const fragment=document.createDocumentFragment();
    if(!run?.events?.length){const tr=document.createElement('tr'),td=document.createElement('td');td.colSpan=6;td.textContent='尚无事件';tr.append(td);fragment.append(tr);}
    for(const event of run?.events||[]){
      const tr=document.createElement('tr');tr.dataset.status=event.Status;
      for(const value of [event['Detection point'],event['Trigger time'],acquisitionReading(event,'Temperature_°C'),acquisitionReading(event,'Humidity_%RH'),ACQ_STATUS[event.Status]||event.Status,event.Explanation||'']){const td=document.createElement('td');td.textContent=value;tr.append(td);}
      fragment.append(tr);
    }
    $('acq-events').replaceChildren(fragment);
    $('acq-counts').textContent=run?'共'+run.event_count+'条 · '+Object.entries(run.status_counts||{}).map(([key,n])=>(ACQ_STATUS[key]||key)+' '+n).join(' / ')+'；显示最近50条，完整记录在CSV。':'显示最近50条；完整事件均保存在原始CSV。';
  }});
  const showError=error=>{client.error=error.message;client.change(client.state);};
  const purpose=()=>{$('acq-formal').hidden=$('acq-purpose').value!=='room_stationary';$('acq-start').textContent=$('acq-purpose').value==='room_stationary'?'新建正式站点运行':'新建联调运行';};
  $('acq-purpose').addEventListener('change',purpose);purpose();
  $('acq-start-form').addEventListener('submit',async event=>{
    event.preventDefault();
    try{
      const formal=$('acq-purpose').value==='room_stationary';
      const data={action:'start_run',purpose:$('acq-purpose').value,notes:$('acq-notes').value};
      if(formal){data.group_id=$('acq-group').value.trim();data.room_id=$('acq-room').value.trim();data.stations=parseStationCSV($('acq-stations').value);if(!data.group_id||!data.room_id)throw Error('实际组号和唯一房间标识尚未填写。');}
      await client.command(data);
    }catch(error){showError(error);}
  });
  $('acq-stations-file').addEventListener('change',async event=>{try{const file=event.target.files[0];if(!file)return;if(file.size>24000)throw Error('站点CSV文件超过24KiB。');const source=await file.text();parseStationCSV(source);$('acq-stations').value=source;}catch(error){showError(error);}});
  $('acq-run').addEventListener('change',()=>client.select($('acq-run').value));
  for(const [id,action] of [['acq-capture','capture'],['acq-cancel','cancel'],['acq-close','close_run']])$(id).addEventListener('click',()=>client.command({action,...(action==='capture'?{station_id:$('acq-station').value}:{})}).catch(showError));
  for(const kind of ['csv','manifest'])$('acq-'+kind).addEventListener('click',async()=>{try{const file=await client.download(kind),url=URL.createObjectURL(file.blob),link=document.createElement('a');link.href=url;link.download=file.filename;link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);}catch(error){showError(error);}});
  // Preserve global Space emergency-stop handling. Insert text spaces before it
  // prevents the default, without stopping propagation or sending motor commands.
  $('acq-start-form').addEventListener('keydown',event=>{if(event.code==='Space'&&!event.isComposing&&!event.ctrlKey&&!event.metaKey&&!event.altKey&&event.target.matches('textarea,input:not([type])')){const input=event.target;if(typeof input.selectionStart==='number'){input.setRangeText(' ',input.selectionStart,input.selectionEnd,'end');event.preventDefault();}}});
  document.addEventListener('rdk-token',event=>client.setToken(event.detail));
  const timer=setInterval(()=>{if(!document.hidden)client.refresh();},1000);
  window.addEventListener('pagehide',()=>clearInterval(timer));
  client.refresh();
}
if(typeof module!=='undefined')module.exports={AcquisitionClient,parseStationCSV,acquisitionReading};
if(typeof document!=='undefined')mountAcquisition();
