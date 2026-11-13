#!/usr/bin/env python3
"""Explicit commissioned RDK runner. Default is validation ONLY; no autostart.

Uses the existing localhost monitor's leased motor API, never another serial/GPIO
owner. Requires real calibration, station coordinates and an independent fresh
stationarity feed. Current equipment has NOT met these commissioning conditions.
"""
import argparse
from dataclasses import asdict
import json
import os
from pathlib import Path
import signal
import socket
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend'))
from check_mission_ready import load_config,check
from experiment_log import RunLog
from mission_runtime import MissionRuntime,MissionLoop,LeasedMotorPort


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs):raise RuntimeError('local_monitor_redirect_rejected')


class LocalControllerClient:
    def __init__(self,url='http://127.0.0.1:8765',token='',timeout=.2):
        parsed=urllib.parse.urlsplit(url)
        if parsed.scheme!='http' or parsed.hostname!='127.0.0.1' or not parsed.port or parsed.username or parsed.password or parsed.path or parsed.query or parsed.fragment:
            raise ValueError('only_localhost_monitor_allowed')
        self.url=url;self.token=token;self.timeout=timeout;self.key=None
        self.opener=urllib.request.build_opener(urllib.request.ProxyHandler({}),NoRedirect())
    def request(self,path,data=None):
        if path not in ('/api/v1/status','/api/v1/control','/api/v1/experiments'):raise ValueError('unsupported_path')
        if data is not None and path!='/api/v1/control':raise ValueError('only_motor_control_post_supported')
        headers={'Accept':'application/json'}
        if self.token:headers['Authorization']='Bearer '+self.token
        body=None
        if data is not None:
            if not self.key:self.key=self.request('/api/v1/control')['control_key']
            headers.update({'Origin':self.url,'Content-Type':'application/json','X-Control-Key':self.key})
            body=json.dumps(data,allow_nan=False).encode()
        req=urllib.request.Request(self.url+path,data=body,headers=headers)
        try:
            with self.opener.open(req,timeout=self.timeout) as response:
                if response.status!=200 or response.geturl()!=self.url+path or response.headers.get_content_type()!='application/json':raise RuntimeError('invalid_monitor_response')
                raw=response.read(262145)
                if len(raw)>262144:raise RuntimeError('monitor_response_too_large')
                packet=json.loads(raw)
                if packet.get('schema_version')!='1.0' or not isinstance(packet.get('data'),dict):raise RuntimeError('invalid_monitor_envelope')
                return packet['data']
        except urllib.error.HTTPError as exc:
            # Do not include response headers, session IDs, keys, or bearer values.
            raise RuntimeError('monitor_http_'+str(exc.code)) from None
    def command(self,data):return self.request('/api/v1/control',data)
    def status(self):return self.request('/api/v1/status')


def stationarity_feed(path,expected_source):
    def read(now):
        sample=json.loads(Path(path).read_text())
        if sample.get('source_id')!=expected_source:raise ValueError('stationarity_source_changed')
        return sample
    return read


def preflight(client,plan,feed,*,clock,stationary_max_age,model_path=Path('/proc/device-tree/model')):
    if not model_path.is_file() or 'RDK X5' not in model_path.read_text().replace('\x00',''):raise ValueError('runner_must_execute_on_RDK_X5_same_clock_domain')
    if 'SOFTWARE_FIXTURE' in json.dumps(asdict(plan),ensure_ascii=False):raise ValueError('synthetic_plan_cannot_execute_hardware')
    live=client.status();host=live['host'];c=live['control']
    if host.get('is_rdk') is not True or host.get('clock_valid') is not True or host.get('hostname')!=plan.calibration.white_reference['host']['hostname']:
        raise ValueError('host_or_clock_does_not_match_calibration')
    if c.get('enabled') is not True or c.get('armed') is not False or c.get('state')!='stopped' or c.get('fault') or c.get('outputs')!=[0,0,0,0] or any(type(v) is not int for v in c.get('outputs',[])):
        raise ValueError('idle_healthy_unowned_motor_required')
    acquisition=client.request('/api/v1/experiments')
    if acquisition.get('active_run_id') or acquisition.get('busy'):raise ValueError('finish_existing_manual_capture_first')
    sample=feed(clock());now=clock()
    from experiment_log import number
    if sample.get('confirmed') is not True or not 0<=now-number(sample.get('sampled_at'),low=0)<stationary_max_age:
        raise ValueError('independent_fresh_initial_stationarity_required')
    if sample.get('source_id') in ('zero_command','motor_command','timer') or not isinstance(sample.get('source_id'),str) or not sample['source_id'].strip():raise ValueError('independent_stationarity_evidence_required')
    return live


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config',type=Path,required=True)
    parser.add_argument('--stationarity-feed',type=Path)
    parser.add_argument('--stationarity-source-id')
    parser.add_argument('--output-root',type=Path,default=Path('/home/sunrise/rbe201tc-sensor-studio/autonomous_runs'))
    parser.add_argument('--execute',action='store_true',help='Only after physical commissioning. Without this, validation only.')
    args=parser.parse_args()
    if not args.execute:
        report=check(args.config);print(json.dumps(report,ensure_ascii=False));return 0 if report['configuration_valid'] else 1
    if not args.stationarity_feed or not args.stationarity_source_id:parser.error('独立停稳输入及来源标识尚未配置，不能执行')
    plan,guide,markers,hashes=load_config(args.config)
    config=json.loads(args.config.read_text());client=LocalControllerClient(token=os.environ.get('DEVICE_CONSOLE_TOKEN',''))
    feed=stationarity_feed(args.stationarity_feed,args.stationarity_source_id)
    preflight(client,plan,feed,clock=time.monotonic,stationary_max_age=config['loop']['stationary_max_age'])
    log=RunLog(args.output_root,association_mode='automatic_stationary',purpose='room_stationary',group_id=plan.group_id,room_id=plan.room_id,
               metadata={'mission_id':plan.mission_id,'stations':[asdict(s) for s in plan.stations],
                         'reference_sha256':hashes,'stationarity_source_id':args.stationarity_source_id,
                         'association':'commissioned ordered crossings; not independent landmark localization'})
    runtime=MissionRuntime(plan,log,LeasedMotorPort(client,allow_motion=True),guide,speed=config['guidance']['speed'],clock=time.monotonic)
    event=threading.Event()
    for signum in (signal.SIGINT,signal.SIGTERM):signal.signal(signum,lambda *_:event.set())
    loop=MissionLoop(runtime,client.status,feed,markers,clock=time.monotonic,wait=event.wait,**config['loop'])
    result=loop.run(event)
    print(json.dumps({'run_id':log.run_id,'directory':str(log.directory),'state':result['state'],'reason':result['reason'],'fault':result['fault']},ensure_ascii=False))
    return 0 if result['state']=='completed' and not result['fault'] else 1


if __name__=='__main__':
    try:raise SystemExit(main())
    except (OSError,ValueError,RuntimeError,KeyError,TypeError) as exc:
        print('Mission refused/stopped: '+str(exc),file=sys.stderr);raise SystemExit(2)
