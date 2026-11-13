from pathlib import Path
import shutil,json,ast
O=Path(__file__).resolve().parent;R=O.parents[1];P=R/'work/device-console-v2'
files=['backend/server.py','backend/basic_line_follower.py','backend/tests/test_static.py','backend/tests/test_corner_line_follower.py','backend/tests/test_basic_line_integration.py','config/hardware_observations.json','frontend/app.js','frontend/tests/app.test.cjs','frontend/control.js','frontend/tests/corner-control.test.cjs','BASIC_LINE_FOLLOWING.md']
for name in files:
 f=O/'before'/name;f.parent.mkdir(parents=True,exist_ok=True);assert not f.exists();shutil.copy2(P/name,f)
def edit(name,old,new,count=1):
 f=P/name;s=f.read_text();assert s.count(old)==count,(name,old,s.count(old));f.write_text(s.replace(old,new))
B='backend/basic_line_follower.py'
edit(B,'channel_permutation=(0, 1, 2, 3)','channel_permutation=(3, 2, 1, 0)')
f=P/B;s=f.read_text();idx=s.index('    def _snapshot_locked(');s=s[:idx]+'''    @property
    def channel_permutation(self):
        """Immutable physical order, shared with the read-only fast display."""
        return self._channel_permutation

'''+s[idx:];f.write_text(s)
S='backend/server.py';edit(S,"        if path == '/api/v1/control':","""        if path == '/api/v1/line-sensors':
            reading = self.server.service.sensors.one('line_sensors')
            order = self.server.driving_mode.follower.channel_permutation
            raw = reading.get('value')
            valid = isinstance(raw, (list, tuple)) and len(raw) == 4 and all(type(v) is int and v in (0, 1) for v in raw)
            self.reply(200, {**{key: reading.get(key) for key in ('status', 'age_seconds', 'monotonic', 'successes', 'error')},
                             'value': [raw[index] for index in order] if valid else None,
                             'raw_value': raw, 'channel_order': ['X' + str(index + 1) for index in order]})
            return
        if path == '/api/v1/control':""")
A='frontend/app.js';edit(A,"    this.token = ''; this.paused", "    this.endpoint='/api/v1/status';\n    this.token = ''; this.paused")
edit(A,"  async refresh() {\n    if (this.busy) return;","""  validPayload(payload) {
    return payload.schema_version === '1.0' && Number.isFinite(Date.parse(payload.generated_at)) &&
      ['health','system','devices','telemetry'].every(key => payload.data?.[key] && typeof payload.data[key] === 'object' && !Array.isArray(payload.data[key]));
  }
  nextDelay() { return this.interval; }
  async refresh() {
    if (this.busy) return;
    const started=performance.now();""")
edit(A,"await this.fetch('/api/v1/status',","await this.fetch(this.endpoint,")
edit(A,"      if (payload.schema_version !== '1.0' || !Number.isFinite(Date.parse(payload.generated_at)) ||\n          !['health','system','devices','telemetry'].every(key => payload.data?.[key] && typeof payload.data[key] === 'object' && !Array.isArray(payload.data[key]))) throw Error('schema');","      if (!this.validPayload(payload)) throw Error('schema');")
edit(A,"setTimeout(() => this.refresh(), this.interval);","setTimeout(() => this.refresh(), this.nextDelay(started));")
f=P/A;s=f.read_text();idx=s.index('function thermalSummary(');s=s[:idx]+'''class LinePoller extends Poller {
  constructor(options={}) {
    super({interval:20,timeout:1000,...options});
    this.endpoint='/api/v1/line-sensors';
  }
  nextDelay(started) { return Math.max(0,this.interval-(performance.now()-started)); }
  validPayload(payload) {
    const s=payload.data;
    return payload.schema_version==='1.0' && Number.isFinite(Date.parse(payload.generated_at)) && s &&
      ['live','stale','error','starting','disabled'].includes(s.status) &&
      Array.isArray(s.channel_order) && s.channel_order.length===4 &&
      (s.status!=='live' || (Array.isArray(s.value) && s.value.length===4 &&
        s.value.every(v=>v===0||v===1) && Number.isFinite(s.age_seconds) && s.age_seconds>=0));
  }
}
'''+s[idx:];s=s.replace('Poller, thermalSummary};','Poller, LinePoller, thermalSummary};');f.write_text(s)
# Fast display is the sole owner of the four lamps; slow whole-system snapshots cannot overwrite it.
edit(A,"    badge('line-status',line);badge('camera-status',camera);","    badge('camera-status',camera);")
f=P/A;s=f.read_text();start=s.index('    for(let i=0;i<4;i++){');end=s.index('    const canCamera=',start)
block=s[start:end];s=s[:start]+s[end:]
render='''  function renderLine(state) {
    const elapsed=state.data?Math.max(0,(performance.now()-state.received)/1000):0;
    const line={...state.data?.data,name:'line_sensors'};
    const fresh=state.phase==='live'&&sensorLive(line,elapsed);
    $('line-status').textContent=state.phase==='paused'?'已暂停':fresh?'实时采样':state.phase==='error'?'连接中断':'等待读取';
    $('line-status').className='sensor-badge '+(fresh?'live':'stale');
    $('line-status').title=line.error||'';
    for(let i=0;i<4;i++){
      const v=fresh?line.value[i]:null;
      probes[i].className='probe '+(v===1?'high':v===0?'low':'unknown');
      probes[i].querySelector('span').textContent=line.channel_order?.[i]||'—';
      probes[i].querySelector('.probe-lamp').textContent=v===0||v===1?String(v):'—';
      probes[i].querySelector('small').textContent=v===1?'高电平':v===0?'低电平':'未读取';
      probes[i].title=['左外','左内','右内','右外'][i]+' · '+(line.channel_order?.[i]||'—')+' · '+(v===0?'黑线':v===1?'地面':'未读取');
    }
    $('line-message').textContent=line.error||(!fresh?'当前无有效实时读数。':'四点按车身前进方向从左到右显示。');
  }
  const linePoll=new LinePoller({change:renderLine});
'''
idx=s.index('  const poll=new Poller(');s=s[:idx]+render+s[idx:]
s=s.replace("$('refresh').addEventListener('click',()=>poll.refresh());","$('refresh').addEventListener('click',()=>{poll.refresh();linePoll.refresh();});")
s=s.replace("$('pause').addEventListener('click',()=>poll.paused?poll.resume():poll.pause());","$('pause').addEventListener('click',()=>{if(poll.paused){poll.resume();linePoll.resume();}else{poll.pause();linePoll.pause();}});")
s=s.replace('framePoll.setEnabled(false);poll.setToken(token);','framePoll.setEnabled(false);poll.setToken(token);linePoll.setToken(token);').replace("framePoll.setEnabled(false);poll.setToken('');","framePoll.setEnabled(false);poll.setToken('');linePoll.setToken('');")
s=s.replace('const clock=setInterval(()=>render(poll.state),250);','const clock=setInterval(()=>{render(poll.state);renderLine(linePoll.state);},250);')
s=s.replace("poll.pause();poll.token='';if(frameURL)","poll.pause();poll.token='';linePoll.pause();linePoll.token='';if(frameURL)")
s=s.replace("  poll.refresh();\n}","  document.addEventListener('visibilitychange',()=>{if(document.hidden)linePoll.pause();else if(!poll.paused)linePoll.resume();});\n  poll.refresh();linePoll.refresh();\n}")
f.write_text(s)
# Revert only mapping-specific expectations; all15 rules and new forward-WHITE behavior remain.
C='backend/tests/test_corner_line_follower.py';f=P/C;s=f.read_text();start=s.index('class WireMappingTests');end=s.index("\n\nif __name__",start);part=s[start:end]
part=part.replace('User confirmed left-to-right X1/X2/X3/X4; no reversal.','Live physical-leftmost black is raw X4; physical order X4/X3/X2/X1.')
part=part.replace('(([0, 0, 0, 1], [0, 0, 0, 1], (-50, 50)),\n                                    ([1, 0, 0, 0], [1, 0, 0, 0], (50, -50)),\n                                    ([0, 1, 0, 1], [0, 1, 0, 1], (-50, 50)),\n                                    ([1, 0, 1, 0], [1, 0, 1, 0], (50, -50)))','(([0, 0, 0, 1], [1, 0, 0, 0], (50, -50)),\n                                    ([1, 0, 0, 0], [0, 0, 0, 1], (-50, 50)),\n                                    ([0, 1, 0, 1], [1, 0, 1, 0], (50, -50)),\n                                    ([1, 0, 1, 0], [0, 1, 0, 1], (-50, 50)))')
# First default assertion changes; explicit identity test at the end stays identity.
part=part.replace("self.assertEqual(state['channel_order'], ['X1', 'X2', 'X3', 'X4'])","self.assertEqual(state['channel_order'], ['X4', 'X3', 'X2', 'X1'])",1)
part=part.replace('user_confirmed_left_to_right_order','physical_leftmost_x4_order').replace('expected = ((-50, 50), (0, 50), (50, 0), (50, -50))','expected = ((50, -50), (50, 0), (0, 50), (-50, 50))').replace("state['last_values'].index(0), index","state['last_values'].index(0), 3-index")
part=part.replace('self.assertEqual(self.worker._values, (0, 0, 0, 1))','self.assertEqual(self.worker._values, (1, 0, 0, 0))').replace("self.worker.snapshot()['last_values'], [0, 0, 0, 1]","self.worker.snapshot()['last_values'], [1, 0, 0, 0]")
part=part.replace('(([1, 0, 1, 1], (0, 50)), ([1, 1, 0, 1], (50, 0)))','(([1, 0, 1, 1], (50, 0)), ([1, 1, 0, 1], (0, 50)))').replace('test_explicit_identity_matches_production_default','test_explicit_identity_is_a_logical_policy_fixture')
s=s[:start]+part+s[end:];f.write_text(s)
I='backend/tests/test_basic_line_integration.py';f=P/I;s=f.read_text();start=s.index('class PhysicalWiringBridgeTests');end=s.index('\n\nclass Store:',start);part=s[start:end]
part=part.replace("list(raw))","list(reversed(raw)))").replace("state['last_values'],raw)","state['last_values'],list(reversed(raw)))")
# Raw values remain raw, including check_wire cached raw assertions.
part=part.replace("state['last_raw_values'], list(reversed(raw))","state['last_raw_values'], list(raw)").replace("cached['last_raw_values'], list(reversed(raw))","cached['last_raw_values'], list(raw)")
part=part.replace("['X1','X2','X3','X4'])\n        self.assertEqual(state['raw_channel_order']","['X4','X3','X2','X1'])\n        self.assertEqual(state['raw_channel_order']",1)
# Replace the complete explicit raw->UART matrix using a fixed physical matrix, independent of production steering.
mstart=part.index('        cases = (');mend=part.index('\n        for raw, expected in cases:',mstart)
physical_pairs={'0000':[50,50,-50,-50],'0001':[-50,-50,50,50],'0010':[50,50,-50,-50],'0011':[-50,-50,50,50],'0100':[50,50,-50,-50],'0101':[-50,-50,50,50],'0110':[50,50,-50,-50],'0111':[-50,-50,50,50],'1000':[50,50,-50,-50],'1001':[50]*4,'1010':[50,50,-50,-50],'1011':[0,0,50,50],'1100':[50,50,-50,-50],'1101':[50,50,0,0],'1110':[50,50,-50,-50]}
cases=[(list(map(int,key[::-1])),value) for key,value in physical_pairs.items()]
part=part[:mstart]+'        cases = '+repr(cases)+part[mend:]
part=part.replace("self.assertEqual(state['last_values'],raw)","self.assertEqual(state['last_values'],list(reversed(raw)))")
part=part.replace("test_raw_0001_turns_left(self): self.check_wire([0,0,0,1], 'left')","test_raw_0001_turns_right(self): self.check_wire([0,0,0,1], 'right')").replace("test_raw_1000_turns_right(self): self.check_wire([1,0,0,0], 'right')","test_raw_1000_turns_left(self): self.check_wire([1,0,0,0], 'left')").replace("test_raw_0101_turns_left(self): self.check_wire([0,1,0,1], 'left')","test_raw_0101_turns_right(self): self.check_wire([0,1,0,1], 'right')").replace("test_raw_1010_turns_right(self): self.check_wire([1,0,1,0], 'right')","test_raw_1010_turns_left(self): self.check_wire([1,0,1,0], 'left')")
part=part.replace("requested_outputs'],[50,50,-50,-50]","requested_outputs'],[-50,-50,50,50]").replace("b'$spd:200,200,-200,-200#'","b'$spd:-200,-200,200,200#'")
part=part.replace("([0,0,0,1],[-50,-50,50,50]),\n                ([1,1,1,1],[-50,-50,50,50])","([0,0,0,1],[50,50,-50,-50]),\n                ([1,1,1,1],[50,50,-50,-50])")
s=s[:start]+part+s[end:];f.write_text(s)
edit('frontend/control.js','车身左→右为X1→X4','车身左→右为X4→X1');edit('frontend/tests/corner-control.test.cjs','车身左→右为X1→X4','车身左→右为X4→X1')
f=P/'config/hardware_observations.json';d=json.loads(f.read_text());l=d['line_sensors'];l['physical_left_to_right_order']=['X4','X3','X2','X1'];l['raw_to_physical_left_to_right_indices']=[3,2,1,0];l['physical_order_source']='current_user_physical_leftmost_black_with_forward_view_and_live_gpio_20261113';l['physical_order_previous_assumption']='Identity was applied from nonspecific reversal feedback; superseded by current physically identified X4 leftmost.';l['physical_order_evidence']=['outputs/RBE201TC_LINE_DISPLAY_CHECK_20261113/leftmost_black_samples.json','User confirms leftmost from vehicle rear looking in forward travel direction.','Older complete stationary sweep supports remaining channel order.'];l['verification_scope']='Current left outer X4 independently matched to five live raw1110 samples and user physical forward-view confirmation. Remaining order follows archived full sweep; no new four-position sweep or powered motion performed.';f.write_text(json.dumps(d,ensure_ascii=False,indent=2)+'\n')
f=P/'BASIC_LINE_FOLLOWING.md';s=f.read_text();old=next(x for x in s.splitlines() if x.startswith('2026-11-13最新：'));s=s.replace(old,'2026-11-13最新静态核对：用户从车尾按前进方向看、只让左外探头压黑，五次实时raw均1110，确认左外是X4。车身左→右恢复X4/X3/X2/X1，入口[3,2,1,0]；此前identity更正没有静态实物锚点，已被当前对应核对推翻。其余顺序沿历史完整横扫记录；本次只独立重测左外，不冒称新测了全部四位。BOARD15/18/16/11 raw不改。');s+='\n探头网页显示由只读轻量接口/api/v1/line-sensors读取已按同一生产映射排序的四位，目标20ms刷新一次，扣除请求耗时且始终单请求。其他系统状态仍700ms。黑白CSS不变、GPIO采样和循线周期不变；实际显示率受网络往返和浏览器调度影响。\n';f.write_text(s)
print('Implemented shared physical display order and fast read-only 20ms polling; CSS and color classes unchanged.')
