from pathlib import Path
import json,datetime,zoneinfo,statistics,collections
O=Path(__file__).resolve().parent;data=json.loads((O/'trials_export.json').read_text());old={t['summary']['id'] for t in json.loads((O.parent/'RBE201TC_TRIAL_ANALYSIS_20261113/trials_export.json').read_text())['trials']};tz=zoneinfo.ZoneInfo('Asia/Shanghai');out=[]
for t in sorted(data['trials'],key=lambda t:t['summary']['started_at']):
 s=t['summary'];ev=t['events'];samples=[e for e in ev if e['event']=='sample']
 if s['id'] in old:continue
 start=s['start_monotonic'];changes=[];last=None
 for e in samples:
  key=(tuple(e['values']),e['reason'],tuple(e['motor']['outputs']))
  if key!=last:
   changes.append(dict(t=round(e['monotonic']-start,5),gpio_t=round(e['sample_monotonic']-start,5),count=e['sample_count'],physical=''.join(map(str,e['values'])),raw=''.join(map(str,e['raw_values'])),reason=e['reason'],pair=[e['left'],e['right']],outputs=e['motor']['outputs'],gap=round(e['line_gap_elapsed_s'],5),turn=e.get('turn_direction'),centers=e.get('center_samples'),sample_to_sent_ms=round(1000*(e['monotonic']-e['sample_monotonic']),3)))
  last=key
 pivots=[]
 for e in samples:
  if e['left']*e['right']<0 and (not pivots or pivots[-1].get('closed',False) or pivots[-1]['pair']!=[e['left'],e['right']]):
   pivots.append(dict(first=e['monotonic'],last=e['monotonic'],gpio_first=e['sample_monotonic'],pair=[e['left'],e['right']],n=1,first_pattern=e['values'],first_raw=e['raw_values'],first_outputs=e['motor']['outputs'],sample_to_sent_ms=1000*(e['monotonic']-e['sample_monotonic']),closed=False,boost_samples=int(e['motor']['startup_boost_active'])))
  elif pivots and not pivots[-1]['closed'] and e['left']*e['right']<0:
   pivots[-1]['last']=e['monotonic'];pivots[-1]['n']+=1;pivots[-1]['boost_samples']+=int(e['motor']['startup_boost_active'])
  elif pivots and not pivots[-1]['closed']:pivots[-1]['closed']=True
 for p in pivots:p.update(observed_span=p['last']-p['first'],first_relative=p['first']-start,last_relative=p['last']-start)
 failure=next((e for e in ev if e['event']=='failure'),{})
 distinct=[]
 for e in samples:
  if not distinct or distinct[-1]['sample_count']!=e['sample_count']:distinct.append(e)
 skips=[b['sample_count']-a['sample_count']-1 for a,b in zip(distinct,distinct[1:])]
 r=dict(id=s['id'],time=datetime.datetime.fromisoformat(s['started_at']).astimezone(tz).strftime('%H:%M:%S'),speed=s['configuration']['speed'],duration=s['duration_s'],result=s.get('result'),stop=s.get('stop_reason'),events_complete=len(ev)==s['event_count'],samples=len(samples),left_outer_frames=sum(e['values'][0]==0 for e in samples),right_outer_frames=sum(e['values'][3]==0 for e in samples),raw_X4_black_frames=sum(e['raw_values'][3]==0 for e in samples),hard_frames=sum(e['left']*e['right']<0 for e in samples),pivots=pivots,transitions=changes,failure=failure,skipped_gpio_sequences=sum(skips),max_skipped_between_reads=max(skips,default=0),gpio_to_sent_ms_median=statistics.median(e['monotonic']*1000-e['sample_monotonic']*1000 for e in samples) if samples else None,mapping_mismatches=sum(e['values']!=e['raw_values'][::-1] for e in samples),target_mismatches=sum(e['motor']['requested_outputs']!=[e['left'],e['left'],e['right'],e['right']] for e in samples))
 out.append(r)
(O/'new_trial_metrics.json').write_text(json.dumps(out,ensure_ascii=False,indent=2)+'\n')
print('New',len(out),'moving',sum(bool(r['samples']) for r in out),'complete',all(r['events_complete'] for r in out),'bad map/targets',sum(r['mapping_mismatches']+r['target_mismatches'] for r in out))
for r in out:
 print(r['time'],'speed',r['speed'],'samples',r['samples'],'L/R outer',r['left_outer_frames'],r['right_outer_frames'],'hardframes',r['hard_frames'],'stop',r['stop'],'gap',round(r['failure'].get('line_gap_elapsed_s',0),4),'skipped',r['skipped_gpio_sequences'])
 print(' pivots',[(round(p['first_relative'],3),round(p['observed_span'],3),round(p['sample_to_sent_ms'],1),p['boost_samples']) for p in r['pivots']])
 if r['speed']==20 or r['time']=='15:08:54':
  for x in r['transitions']:print(' ',x)
  print(' failure',{k:r['failure'].get(k) for k in ['monotonic','reason','values','raw_values','line_gap_elapsed_s','last_visible_values','sample_monotonic','sample_count']})
