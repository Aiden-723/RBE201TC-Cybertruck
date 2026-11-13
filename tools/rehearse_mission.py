#!/usr/bin/env python3
"""SOFTWARE FIXTURE ONLY. Runs the full task/recorder chain without hardware/network.

Invented calibration/locations/timing are never production defaults. All CSVs
remain bench_diagnostic and live under an explicitly synthetic output directory.
"""
import argparse
from datetime import datetime,timedelta,timezone
import json
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend'))
from experiment_log import RunLog
from line_guidance import LineGuidance,OrderedCrossings
from mission_plan import CalibrationEvidence,StationPlan,Timing,MissionPlan,Observation,validate_plan
from mission_runtime import MissionRuntime,SimulatedMotion

LABEL='SOFTWARE_FIXTURE_NOT_COURSE_DATA'
CASES=('success','sensor_failure','lost_line','missing_stationarity','timeout','reboot','emergency_stop')


def fixture_reference(surface,values):
    start=datetime(2026,11,6,tzinfo=timezone.utc)
    return dict(reference=surface,user_confirmed_ready=True,errors=[],host=dict(is_rdk=True,hostname=LABEL,model='D-Robotics RDK X5 V1.0'),
                samples=[dict(value=values,physical_pins=[15,18,16,11],monotonic=10+i*.125,successes=i+1,age_seconds=.1,sampled_at=(start+timedelta(seconds=i*.125)).isoformat()) for i in range(20)])


def fixture_plan(case):
    return MissionPlan('SOFTWARE_FIXTURE_'+case,'SOFTWARE_FIXTURE_GROUP','SOFTWARE_FIXTURE_ROOM',
        tuple(StationPlan('SOFTWARE_FIXTURE_S'+str(i),'SOFTWARE_FIXTURE_M'+str(i),float(i),0.,.2) for i in (1,2)),
        Timing(.3,.6,1.5,.3,.5,2.,30.),
        CalibrationEvidence(fixture_reference('white_paper',[0,0,0,0]),fixture_reference('black_tape',[1,1,1,1]),('X1','X2','X3','X4'),LABEL),
        LABEL,LABEL,LABEL,LABEL)


def fixture_status(now,sequence,wall):
    return dict(host=dict(is_rdk=True,clock_valid=True,device_time=wall.isoformat()),control=dict(enabled=True,state='stopped',outputs=[0,0,0,0],fault=None),
                system=dict(uptime_seconds=dict(value=now)),sensors=dict(dht11=dict(status='live',value=dict(temperature_c=24.,humidity_pct=50.),
                monotonic=now-.01,sampled_at=wall.isoformat(),age_seconds=.01,successes=sequence,failures=0)))


def run_case(parent,case):
    if case not in CASES:raise ValueError('Unknown fixture case')
    plan=fixture_plan(case);calibration,source=validate_plan(plan)
    now=[100.];base=datetime(2026,11,6,tzinfo=timezone.utc)
    wall=lambda:base+timedelta(seconds=now[0])
    parent=Path(parent)/LABEL/case
    log=RunLog(parent,association_mode='automatic_stationary',purpose='bench_diagnostic',run_id=plan.mission_id,now=wall,metadata={'synthetic':True,'warning':LABEL,'scenario':case})
    guide=LineGuidance(calibration,positions=(-3.,-1.,1.,3.),deadband=.5)
    motion=SimulatedMotion();runtime=MissionRuntime(plan,log,motion,guide,speed=100,clock=lambda:now[0])
    markers=OrderedCrossings([s.marker_id for s in plan.stations],confirm_seconds=.2,confirm_frames=3,clear_frames=2,max_gap=.5)
    traces=[];following={};stop_started=None;frozen=None
    for seq in range(1,301):
        now[0]=100+seq*.1
        state=runtime.planner.state;index=runtime.planner.index
        if state in ('idle','following'):
            following[index]=following.get(index,0)+1
            raw=(0,1,1,0) if following[index]<=5 else (1,1,1,1)
        else:raw=(1,1,1,1)
        if case=='lost_line' and seq==4:raw=(0,0,0,0)
        g=guide.classify(raw);marker=markers.update(g.kind=='crossing',now[0])
        if state=='stopping' and stop_started is None:stop_started=now[0]
        # Independent synthetic stationarity signal; never copy zero_command.
        stationary=stop_started is not None and now[0]-stop_started>=.25 and case!='missing_stationarity'
        if state=='following':stop_started=None;stationary=False
        snapshot=fixture_status(now[0],seq,wall())
        if runtime.capture and frozen is None:
            frozen=dict(snapshot['sensors']['dht11'],monotonic=runtime.capture.marker-.01,successes=runtime.capture.counts[0],failures=runtime.capture.counts[1])
        if runtime.capture and case=='timeout':snapshot['sensors']['dht11']=dict(frozen)
        if runtime.capture and case=='reboot':snapshot['system']['uptime_seconds']['value']=1.
        if runtime.capture and case=='sensor_failure':
            snapshot['sensors']['dht11'].update(status='error',value=None,error='SOFTWARE_FIXTURE_checksum_failure',failures=1)
        observation=Observation(now[0],seq,source,raw,(15,18,16,11),g.kind in ('line','crossing'),motion.stopped,stationary,True,False,marker)
        if case=='emergency_stop' and state=='adapting':result=runtime.cancel(emergency=True)
        else:result=runtime.tick(observation,snapshot,now=now[0])
        traces.append(dict(sequence=seq,monotonic=now[0],raw_line=list(raw),guidance=g.kind,marker=marker,
                           independent_fixture_stationary=stationary,state=result['state'],station_index=result['station_index'],fault=result['fault']))
        if result['closed']:break
    if not runtime.closed:runtime.cancel()
    result=dict(scenario=case,synthetic=True,warning=LABEL,**runtime.snapshot(),trace_frames=len(traces),motion_commands=motion.commands)
    (log.directory/'trace.json').write_text(json.dumps(traces,ensure_ascii=False,indent=2))
    (log.directory/'rehearsal.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
    return log,runtime,traces


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--case',choices=CASES,default='success')
    parser.add_argument('--all',action='store_true')
    args=parser.parse_args()
    for case in CASES if args.all else (args.case,):
        log,runtime,trace=run_case(args.output,case)
        print(json.dumps({'case':case,'state':runtime.snapshot()['state'],'reason':runtime.snapshot()['reason'],'fault':runtime.fault,'rows':len(log.records),'path':str(log.directory),'hardware':False}))


if __name__=='__main__':main()
