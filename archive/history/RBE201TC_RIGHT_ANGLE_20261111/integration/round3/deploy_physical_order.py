"""Guarded controller-only v3 update; --check is offline, --apply restarts the existing unit."""
import argparse
import base64
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[4]
QA = Path(__file__).resolve().parent
PROJECT = ROOT / 'work/device-console-v2'
FILES = ['backend/basic_line_follower.py', 'backend/tests/test_basic_line_follower.py',
         'backend/tests/test_corner_line_follower.py', 'backend/tests/test_basic_line_integration.py',
         'frontend/control.js', 'frontend/tests/corner-control.test.cjs',
         'BASIC_LINE_FOLLOWING.md', 'config/hardware_observations.json']

MANAGED_LAUNCH = r'''def service_pid(name):
 return int(subprocess.check_output(['systemctl','show',name,'-p','MainPID','--value'],text=True))
class ManagedProcess:
 def __init__(self,pid):self.pid=pid
 def poll(self):
  return None if pathlib.Path('/proc/%d'%self.pid).exists() and service_pid('rbe201tc-monitor.service')==self.pid else 0
 def terminate(self):
  assert pathlib.Path('/proc/%d/cwd'%self.pid).resolve()==root
  os.kill(self.pid,signal.SIGTERM)
 def wait(self,timeout=8):
  end=time.monotonic()+timeout
  while self.poll() is None and time.monotonic()<end:time.sleep(.05)
  if self.poll() is None:raise RuntimeError('Managed controller did not exit')
  return 0
 def __repr__(self):return 'ExistingMonitorUnit(pid=%d)'%self.pid

def launch(label):
 subprocess.run(['sudo','-n','/usr/bin/systemctl','restart','rbe201tc-monitor.service'],check=True,timeout=20,capture_output=True,text=True)
 end=time.monotonic()+8
 while time.monotonic()<end:
  candidate=service_pid('rbe201tc-monitor.service')
  if candidate>0 and pathlib.Path('/proc/%d'%candidate).exists():
   assert pathlib.Path('/proc/%d/cwd'%candidate).resolve()==root
   assert 'rbe201tc-monitor.service' in pathlib.Path('/proc/%d/cgroup'%candidate).read_text()
   assert service_pid('rbe201tc-sensors.service')==PAYLOAD['sensor_service_pid'],'Sampler owner changed'
   return ManagedProcess(candidate)
  time.sleep(.05)
 raise RuntimeError('Existing controller unit did not acquire a main PID')
'''


def once(text, old, new):
    assert text.count(old) == 1, ('Unexpected baseline template', old)
    return text.replace(old, new, 1)


def program():
    spec = importlib.util.spec_from_file_location('reviewed_basic_deployer', ROOT/'outputs/RBE201TC_BASIC_LINE_FOLLOWING_20261111/integration/deploy_basic_line.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    remote = module.REMOTE
    remote = once(remote, 'assert len(allowed)==10', 'assert len(allowed)==%d'%len(FILES))
    old_launch = """def launch(label):
 log=(backup/(label+'.log')).open('ab')
 try:return subprocess.Popen(argv,cwd=root,stdin=subprocess.DEVNULL,stdout=log,stderr=subprocess.STDOUT,start_new_session=True,env={**os.environ,'PYTHONDONTWRITEBYTECODE':'1'})
 finally:log.close()
"""
    remote = once(remote, old_launch, MANAGED_LAUNCH)
    remote = once(remote, "c=get('/api/v1/control');a=get('/api/v1/experiments')", "c=get('/api/v1/control');a=get('/api/v1/experiments')\n assert not c.get('autonomy',{}).get('running',False),'Autonomy running'")
    remote = once(remote, 'new=None;installed=[]', "assert service_pid('rbe201tc-monitor.service')==pid\nassert subprocess.check_output(['systemctl','show','rbe201tc-monitor.service','-p','Restart','--value'],text=True).strip()=='on-failure'\nassert service_pid('rbe201tc-sensors.service')==PAYLOAD['sensor_service_pid']\nnew=None;installed=[]")
    remote = once(remote, 'wait_healthy(old,False)', 'wait_healthy(old,True)')
    remote = once(remote, 'c,a,s=wait_healthy(new,True)', "c,a,s=wait_healthy(new,True)\n assert c['autonomy']['channel_permutation']==[3,2,1,0]\n assert c['autonomy']['channel_order']==['X4','X3','X2','X1']\n assert c['autonomy']['raw_channel_order']==['X1','X2','X3','X4']\n assert c['autonomy']['corner_max_seconds']==1.4 and c['autonomy']['period_s']==.05\n assert c['autonomy']['fallback_used'] is False\n assert service_pid('rbe201tc-sensors.service')==PAYLOAD['sensor_service_pid']")
    remote = once(remote, "'motion_commands_sent_by_deployer':0,", "'motion_commands_sent_by_deployer':0,'controller_service':'rbe201tc-monitor.service','sensor_service_pid_unchanged':PAYLOAD['sensor_service_pid'],")
    pre = json.loads((QA/'production_before.json').read_text())
    for name, wanted in pre['protected'].items():
        assert hashlib.sha256((PROJECT/name).read_bytes()).hexdigest() == wanted, ('Protected local change', name)
    payload = dict(root=pre['root'], pid=pre['pid'], argv=pre['argv'],
                   sensor_service_pid=pre['sensor_service_pid'],
                   before={n:info['sha256'] for n,info in pre['before'].items()},
                   protected=pre['protected'],
                   files={n:dict(content=base64.b64encode((PROJECT/n).read_bytes()).decode(),
                                 sha256=hashlib.sha256((PROJECT/n).read_bytes()).hexdigest()) for n in FILES})
    result = 'import json\nPAYLOAD=json.loads('+repr(json.dumps(payload))+')\n'+remote
    compile(result, 'guarded_existing_service_physical_order', 'exec')
    return result, payload


def main():
    cli = argparse.ArgumentParser(description=__doc__)
    group = cli.add_mutually_exclusive_group(required=True)
    group.add_argument('--check', action='store_true')
    group.add_argument('--apply', action='store_true')
    args = cli.parse_args()
    rendered, payload = program()
    if args.check:
        report = dict(compile='pass', files=len(FILES), protected=len(payload['protected']),
                      program_sha256=hashlib.sha256(rendered.encode()).hexdigest(),
                      existing_service_only=True, sampler_restarts=0,
                      source_hashes={n:x['sha256'] for n,x in payload['files'].items()})
        with (QA/'deployment_dry_check.json').open('x') as f:json.dump(report,f,indent=2)
        print(json.dumps(report));return
    command = ['ssh','-F','/Users/your-user/Library/Application Support/RBE201TC/connection/autoboat_ssh_config',
               '-o','ClearAllForwardings=yes','-o','BatchMode=yes','-o','StrictHostKeyChecking=yes',
               '-o','ConnectTimeout=4','rbe201tc-wifi','python3','-B','-']
    run = subprocess.run(command,input=rendered,capture_output=True,text=True,timeout=180)
    with (QA/'robot_deployment.log').open('x') as f:f.write(run.stdout+run.stderr+'\n[exit code: %d]\n'%run.returncode)
    print(run.stdout);print(run.stderr);print('[exit code: %d]'%run.returncode)
    if run.returncode:raise SystemExit(run.returncode)
    with (QA/'robot_deployment_result.json').open('x') as f:json.dump(json.loads(run.stdout),f,ensure_ascii=False,indent=2)


if __name__ == '__main__':main()
