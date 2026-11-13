'use strict';
const TRIAL_RESULTS={passed:'正常通过',straight_drift:'直线偏离',overshot:'冲出黑线',underturn:'转弯不足／中途停住',oscillation:'转过头／来回摆动',no_motion:'轮子不动／起步困难',manual_stop:'主动结束测试',custom:'自行填写'};
class TrialsClient {
  constructor({fetch=globalThis.fetch.bind(globalThis),change=()=>{}}={}){this.fetch=fetch;this.change=change;this.token='';this.key='';this.epoch=0;this.busy=false;this.state={enabled:false,records:[]};}
  async request(path,data){
    const controller=new AbortController(),timer=setTimeout(()=>controller.abort(),5000);
    try{
      const headers=this.token?{Authorization:'Bearer '+this.token}:{};
      if(data)Object.assign(headers,{'Content-Type':'application/json','X-Control-Key':this.key});
      const response=await this.fetch(path,{method:data?'POST':'GET',headers,cache:'no-store',credentials:'omit',redirect:'error',signal:controller.signal,...(data?{body:JSON.stringify(data)}:{})});
      const payload=await response.json();if(!response.ok)throw Error(payload.error?.code||'trial_request_failed');return payload.data;
    }finally{clearTimeout(timer);}
  }
  async refresh(){
    if(this.busy)return;this.busy=true;const epoch=this.epoch;
    try{const data=await this.request('/api/v1/line-trials');if(epoch!==this.epoch)return;
      const {annotation_key,...state}=data;this.key=annotation_key||'';this.state={...state,error:state.error||null};this.change(this.state);
    }catch(error){if(epoch===this.epoch){this.state={...this.state,error:'记录连接中断'};this.change(this.state);}}
    finally{this.busy=false;}
  }
  setToken(token){this.epoch++;this.token=token;this.key='';this.state={enabled:false,records:[]};this.change(this.state);}
  async save(id,category,note){
    if(!Object.hasOwn(TRIAL_RESULTS,category)||typeof note!=='string'||note.length>2000||(category==='custom'&&!note.trim()))throw Error('请选择结果或填写实际情况');
    const epoch=this.epoch;const result=await this.request('/api/v1/line-trials',{trial_id:id,category,note});
    if(epoch!==this.epoch)throw Error('连接设置已变化，请重新保存');await this.refresh();return result;
  }
  async export(){return this.request('/api/v1/line-trials/export');}
}
if(typeof module!=='undefined')module.exports={TrialsClient,TRIAL_RESULTS};
function mountTrials(){
  const $=id=>document.getElementById(id),dialog=$('trial-dialog');let current=null,saving=false,motion={armed:true};
  const dismissed=new Set();
  const node=(tag,text)=>{const n=document.createElement(tag);n.textContent=text;return n;};
  const date=text=>new Date(text).toLocaleString('zh-CN',{hour12:false});
  const duration=value=>Number.isFinite(value)?value.toFixed(1)+'秒':'—';
  const ready=()=>!motion.armed&&!motion.fault&&['stopped','disabled'].includes(motion.state)&&(motion.outputs||[]).every(v=>v===0);
  function open(record){
    if(client.state.active_id||!ready()||dialog.open)return;
    current=record;$('trial-dialog-title').textContent='这次测试的结果是什么？';
    $('trial-dialog-info').textContent=date(record.started_at)+' · '+duration(record.duration_s);
    $('trial-result').value=record.result?.category||'';$('trial-note').value=record.result?.note||'';
    $('trial-save-message').textContent='';updateSave();dialog.showModal();$('trial-result').focus();
  }
  function maybePrompt(){
    if(client.state.error||client.state.active_id||!ready()||dialog.open)return;
    const pending=(client.state.records||[]).find(r=>r.status!=='running'&&!r.result&&!dismissed.has(r.id));
    if(pending)open(pending);
  }
  function render(state){
    const records=state.records||[],active=records.find(r=>r.id===state.active_id),pending=records.filter(r=>r.status!=='running'&&!r.result);
    $('trial-status').textContent=state.error?'测试记录暂不可用':!state.enabled?'等待测试记录服务':active?'正在记录本次测试 · '+duration(active.duration_s):'已记录 '+records.length+' 次'+(pending.length?' · '+pending.length+' 次待填写':'');
    $('trial-history-count').textContent=records.length+' 次';$('trial-export').disabled=!records.length||!!state.error;
    const rows=[];
    for(const record of records){
      const row=document.createElement('tr');row.append(node('td',date(record.started_at)),node('td',duration(record.duration_s)));
      const result=node('td',record.status==='running'?'记录中':record.result?.label||'待填写');
      if(record.result?.note){const note=node('small',record.result.note);note.className='trial-note-preview';result.append(note);}
      row.append(result);const cell=node('td',''),button=node('button',record.result?'修改':'填写');button.type='button';button.disabled=record.status==='running';button.addEventListener('click',()=>open(record));cell.append(button);row.append(cell);rows.push(row);
    }
    $('trial-history-body').replaceChildren(...rows);$('trial-empty').hidden=records.length>0;maybePrompt();
  }
  const client=new TrialsClient({change:render});
  for(const [value,label] of Object.entries(TRIAL_RESULTS)){const option=node('option',label);option.value=value;$('trial-result').append(option);}
  function updateSave(){$('trial-save').disabled=saving||!$('trial-result').value||($('trial-result').value==='custom'&&!$('trial-note').value.trim());}
  $('trial-result').addEventListener('change',()=>{updateSave();if($('trial-result').value==='custom')$('trial-note').focus();});
  $('trial-note').addEventListener('input',updateSave);
  $('trial-form').addEventListener('submit',async event=>{
    event.preventDefault();if(saving||!current)return;saving=true;updateSave();
    try{await client.save(current.id,$('trial-result').value,$('trial-note').value);dismissed.add(current.id);dialog.close();current=null;}
    catch(error){$('trial-save-message').textContent='未保存，请保留填写内容并重试。';}
    finally{saving=false;updateSave();}
  });
  const defer=()=>{if(saving)return;if(current)dismissed.add(current.id);dialog.close();current=null;};
  dialog.addEventListener('keydown',event=>{if(ready())event.stopPropagation();});
  $('trial-later').addEventListener('click',defer);dialog.addEventListener('cancel',event=>{event.preventDefault();defer();});
  document.addEventListener('rdk-motion',event=>{motion=event.detail;maybePrompt();});
  document.addEventListener('rdk-token',event=>{dialog.close();current=null;client.setToken(event.detail);client.refresh();});
  $('trial-export').addEventListener('click',async()=>{
    try{const data=await client.export();const url=URL.createObjectURL(new Blob([JSON.stringify(data,null,2)],{type:'application/json'}));const a=document.createElement('a');a.href=url;a.download='循线测试记录_'+new Date().toISOString().replace(/[:.]/g,'-')+'.json';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);}
    catch(error){$('trial-status').textContent='导出失败，请稍后重试。';}
  });
  const timer=setInterval(()=>{if(!document.hidden)client.refresh();},750);
  document.addEventListener('visibilitychange',()=>{if(!document.hidden)client.refresh();});
  window.addEventListener('pagehide',()=>clearInterval(timer));client.refresh();
}
if(typeof document!=='undefined')mountTrials();
