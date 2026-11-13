from pathlib import Path
import json,statistics,collections,hashlib,datetime,zoneinfo
O=Path(__file__).resolve().parent;dataset=json.loads((O/'trials_export.json').read_text());results=[]
for trial in dataset['trials']:
 s=trial['summary'];events=trial['events'];samples=[e for e in events if e['event']=='sample'];groups=[];bad_outputs=0
 for e in samples:
  pair=(e['left'],e['right']);tm=e['recorded_monotonic'];expected=[pair[0],pair[0],pair[1],pair[1]]
  bad_outputs+=e['motor'].get('requested_outputs')!=expected
  if not groups or groups[-1]['pair']!=pair:groups.append(dict(pair=pair,first=tm,last=tm,n=1,patterns=[''.join(map(str,e['values']))],actions=[e['reason']]))
  else:
   g=groups[-1];g['last']=tm;g['n']+=1
   if g['patterns'][-1]!=''.join(map(str,e['values'])):g['patterns'].append(''.join(map(str,e['values'])))
   if e['reason'] not in g['actions']:g['actions'].append(e['reason'])
 start=events[0]['recorded_monotonic']
 for g in groups:g.update(first=g['first']-start,last=g['last']-start,observed_span_s=g['last']-g['first'])
 hard=[g for g in groups if g['pair'][0]*g['pair'][1]<0];flips=[]
 for prev,g in zip(groups,groups[1:]):
  if prev['pair'][0]*prev['pair'][1]<0 and g['pair'][0]*g['pair'][1]<0 and prev['pair'][0]*g['pair'][0]<0:
   flips.append(dict(at=g['first'],previous=prev['pair'],new=g['pair'],trigger=g['patterns'][0]))
 intervals=[b['recorded_monotonic']-a['recorded_monotonic'] for a,b in zip(samples,samples[1:])]
 r=dict(id=s['id'],local_start=datetime.datetime.fromisoformat(s['started_at']).astimezone(zoneinfo.ZoneInfo('Asia/Shanghai')).isoformat(),duration_s=s['duration_s'],result=s['result'],stop_reason=s['stop_reason'],events=len(events),samples=len(samples),events_complete=(len(events)==s['event_count']),mapping_mismatches=sum(e['values']!=list(reversed(e['raw_values'])) for e in samples),requested_output_mismatches=bad_outputs,target_changes=len(groups)-1,transition_resets=samples[-1]['motor']['speed_transition_resets']-samples[0]['motor']['speed_transition_resets'],hard_groups=len(hard),longest_hard_observed_span_s=max((g['observed_span_s'] for g in hard),default=0),direct_hard_flips=flips,command200_samples=sum(any(abs(v)==200 for v in e['motor']['outputs']) for e in samples),median_interval_s=statistics.median(intervals),max_interval_s=max(intervals),left_outer_seen=sum(e['values'][0]==0 for e in samples),right_outer_seen=sum(e['values'][3]==0 for e in samples),terminal_gap_elapsed_s=events[-1].get('line_gap_elapsed_s'),pair_groups=groups)
 results.append(r)
(O/'final_metrics.json').write_text(json.dumps(results,ensure_ascii=False,indent=2)+'\n')
print('Verified trials/events/samples',len(results),sum(r['events'] for r in results),sum(r['samples'] for r in results));print('bad mapping/output',sum(r['mapping_mismatches'] for r in results),sum(r['requested_output_mismatches'] for r in results))
for r in results:print(r['local_start'][11:19],round(r['duration_s'],2),'changes',r['target_changes'],'resets',r['transition_resets'],'200',r['command200_samples'],'/',r['samples'],'hardspan',round(r['longest_hard_observed_span_s'],3),'flips',len(r['direct_hard_flips']))
