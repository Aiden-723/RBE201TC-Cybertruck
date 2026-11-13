"""Compare command behavior for the same old inputs; NOT a closed-loop simulation."""
from pathlib import Path
import importlib.util,json,sys
O=Path(__file__).resolve().parent;P=O.parents[1]/'work/device-console-v2'
sys.path.insert(0,str(P/'backend'))
from basic_line_follower import BasicLineFollower
from motor_control import MotorControl

def load(name,path):
 spec=importlib.util.spec_from_file_location(name,path);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m
old_f=load('frozen_old_follower',O/'before/backend/basic_line_follower.py').BasicLineFollower
old_m=load('frozen_old_motor',O/'before/backend/motor_control.py').MotorControl
class Wire:
 def __init__(self):self.packets=[]
 def send(self,p):self.packets.append(p)
 def close(self):pass

def run(t,F,M):
 samples=[e for e in t['events'] if e['event']=='sample'];now=[t['summary']['start_monotonic']];wire=Wire()
 motor=M(wire,clock=lambda:now[0],mode='speed',startup_boost=True)
 feed={}
 follower=F(motor,lambda:dict(feed),clock=lambda:now[0],threaded=False)
 owner=follower.start(50);out=[];requested=[];stop=None
 try:
  for seq,e in enumerate(samples):
   now[0]=e['monotonic']
   feed.update(status='live',value=e['raw_values'],monotonic=e['sample_monotonic'],
               age_seconds=now[0]-e['sample_monotonic'],successes=e['sample_count'],physical_pins=[15,18,16,11])
   follower.heartbeat(owner,seq);s=follower.tick();ms=motor.snapshot()
   if not s['running']:stop=s['reason'];break
   out.append(ms['outputs']);requested.append(ms['requested_outputs'])
  resets=motor.speed_transition_resets
  return dict(consumed_samples=len(out),input_samples=len(samples),stopped_reason=stop,
              samples_at_200=sum(any(abs(v)==200 for v in x) for x in out),
              requested_changes=sum(a!=b for a,b in zip(requested,requested[1:])),
              driver_clears=resets,
              direct_pivot_reversals=sum(a[0]*a[2]<0 and b[0]*b[2]<0 and a[0]*b[0]<0
                                         for a,b in zip(requested,requested[1:])))
 finally:follower.close();motor.close()
trials=json.loads((O.parent/'RBE201TC_TRIAL_ANALYSIS_20261113/trials_export.json').read_text())['trials']
results=[dict(id=t['summary']['id'],note=t['summary']['result'],baseline=run(t,old_f,old_m),
              candidate=run(t,BasicLineFollower,MotorControl)) for t in trials]
report=dict(description='Exogenous frozen-input replay only: fake UART has no latency, no dynamics, no wheel feedback. New commands would change future sensor inputs. Counts are not physical pass rates.',
            original_trials_modified=False,results=results)
(O/'frozen_input_replay.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
for r in results:
 print(r['id'], 'changes',r['baseline']['requested_changes'],'->',r['candidate']['requested_changes'],
       'clears',r['baseline']['driver_clears'],'->',r['candidate']['driver_clears'],
       'direct pivot flips',r['baseline']['direct_pivot_reversals'],'->',r['candidate']['direct_pivot_reversals'],
       'consumed',r['candidate']['consumed_samples'],'/',r['candidate']['input_samples'])
