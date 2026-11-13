#!/usr/bin/env python3
"""Validate a mission configuration and optionally GET live readiness; never drives."""
import argparse
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import sys
import urllib.request
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend'))
from line_guidance import LineGuidance,OrderedCrossings
from mission_plan import MissionPlan,StationPlan,Timing,CalibrationEvidence,validate_plan

TEMPLATE={
 'schema_version':'1.0','mission_id':None,'group_id':None,'room_id':None,
 'stations':[],
 'calibration':{'white_reference':'calibration/white_reference_20261104.json','black_reference':None,'physical_left_to_right_order':None,'physical_order_evidence_id':None},
 'timing':{k:None for k in Timing.__dataclass_fields__},
 'route_evidence_id':None,'marker_evidence_id':None,'stationary_evidence_id':None,'timing_evidence_id':None,
 'guidance':{'positions':None,'deadband':None,'speed':None},
 'crossings':{'confirm_seconds':None,'confirm_frames':None,'clear_frames':None,'max_gap':None},
 'loop':{'period':None,'stationary_max_age':None},
}


def load_config(path):
    path=Path(path).resolve();data=json.loads(path.read_text())
    if not isinstance(data,dict) or set(data)!=set(TEMPLATE) or data['schema_version']!='1.0':raise ValueError('配置字段或版本不匹配')
    c=data['calibration']
    if not isinstance(c,dict) or set(c)!=set(TEMPLATE['calibration']):raise ValueError('标定字段不完整')
    references={};hashes={}
    for key in ('white_reference','black_reference'):
        if not isinstance(c[key],str) or not c[key].strip():raise ValueError('缺少真实参考文件：'+key)
        p=(path.parent/c[key]).resolve();raw=p.read_bytes();references[key]=json.loads(raw);hashes[str(p)]=hashlib.sha256(raw).hexdigest()
    order=c['physical_left_to_right_order']
    if not isinstance(order,list):raise ValueError('缺少已实测的探头左右顺序')
    plan=MissionPlan(data['mission_id'],data['group_id'],data['room_id'],tuple(StationPlan(**s) for s in data['stations']),
                     Timing(**data['timing']),CalibrationEvidence(references['white_reference'],references['black_reference'],tuple(order),c['physical_order_evidence_id']),
                     *[data[k] for k in ('route_evidence_id','marker_evidence_id','stationary_evidence_id','timing_evidence_id')])
    calibration,source=validate_plan(plan)
    g=data['guidance'];guide=LineGuidance(calibration,positions=g['positions'],deadband=g['deadband'])
    if type(g['speed']) is not int or not 80<=g['speed']<=220:raise ValueError('缺少经实际调试的速度设定（80–220）')
    crossings=OrderedCrossings([s.marker_id for s in plan.stations],**data['crossings'])
    import math
    loop=data['loop']
    if set(loop)!=set(TEMPLATE['loop']) or any(type(v) not in (int,float) or not math.isfinite(v) or v<=0 for v in loop.values()):raise ValueError('缺少有效的循环与独立停稳信号时间参数')
    if loop['period']>=plan.timing.max_update_gap:raise ValueError('执行周期不能超过计划允许的更新间隔')
    return plan,guide,crossings,hashes


def check(path,url=None):
    result={'configuration_valid':False,'autonomous_enabled':False,'sends_motor_commands':False,'blockers':[],
            'physical_limitations':['文件校验不能证明实物标定真实或仍适用于当前安装','需独立停稳信号，零指令不是已停稳','相同横向胶带只按顺序关联，不能独立识别漏掉的物理标记','当前网页不提供自动驾驶启用入口']}
    if not Path(path).is_file():result['blockers'].append('尚未提供真实mission配置；不要把模板或模拟夹具当成配置')
    else:
        try:
            plan,guide,crossings,hashes=load_config(path)
            result.update(configuration_valid=True,mission_id=plan.mission_id,station_count=len(plan.stations),reference_sha256=hashes)
        except (OSError,ValueError,TypeError,KeyError,AttributeError) as exc:result['blockers'].append(str(exc))
    if url:
        # Only status GET; never import a motor driver or send POST.
        try:
            request=urllib.request.Request(url.rstrip('/')+'/api/v1/status',headers={'Accept':'application/json'})
            with urllib.request.urlopen(request,timeout=4) as response:snapshot=json.load(response)['data']
            result['live']={'host':snapshot['host'].get('hostname'),'clock_valid':snapshot['host'].get('clock_valid'),
                            'sensors':{k:{f:s.get(f) for f in ('status','age_seconds')} for k,s in snapshot['sensors'].items()},
                            'control':{k:snapshot['control'].get(k) for k in ('enabled','state','armed','outputs','fault')}}
            if not snapshot['host'].get('clock_valid'):result['blockers'].append('主板时间需要核对')
            if snapshot['sensors'].get('line_sensors',{}).get('status')!='live':result['blockers'].append('循线原始输入不新鲜')
        except (OSError,ValueError,KeyError) as exc:result['blockers'].append('在线读取未完成：'+str(exc))
    return result


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--config',type=Path,default=Path(__file__).resolve().parents[1]/'config/mission.json');p.add_argument('--url');p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();result=check(a.config,a.url)
    a.output.parent.mkdir(parents=True,exist_ok=True)
    with a.output.open('x',encoding='utf-8') as f:json.dump(result,f,ensure_ascii=False,indent=2)
    print(json.dumps(result,ensure_ascii=False))
    return 0 if result['configuration_valid'] and not result['blockers'] else 1


if __name__=='__main__':raise SystemExit(main())
