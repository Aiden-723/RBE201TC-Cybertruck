"""Speed-only static update: existing API speed=84, no process restart or motor commands."""
from pathlib import Path
import base64,hashlib,json,subprocess
ROOT=Path(__file__).resolve().parents[4]
QA=Path(__file__).resolve().parent
P=ROOT/'work/device-console-v2'
old=json.loads((QA.parent/'round3/robot_deployment_result.json').read_text())
pre=json.loads((QA.parent/'round3/production_before.json').read_text())
names=['frontend/control.js','frontend/tests/control.test.cjs','frontend/tests/corner-control.test.cjs','backend/tests/test_basic_line_integration.py','BASIC_LINE_FOLLOWING.md']
sha=lambda b:hashlib.sha256(b).hexdigest()
old_control_test=(P/'frontend/tests/control.test.cjs').read_text().replace("{action:'autonomy_start',speed:84}","{action:'autonomy_start'}").encode()
before={n:old['hashes'].get(n,sha(old_control_test)) for n in names}
protected=dict(pre['protected']);protected.update({n:old['hashes'][n] for n in ['backend/basic_line_follower.py','config/hardware_observations.json']})
for n,h in protected.items():assert sha((P/n).read_bytes())==h,n
payload={'root':old['root'],'pid':old['new_pid'],'sensor_pid':old['sensor_service_pid_unchanged'],'before':before,'protected':protected,'files':{n:{'sha256':sha((P/n).read_bytes()),'content':base64.b64encode((P/n).read_bytes()).decode()} for n in names}}
REMOTE=r'''
import pathlib,hashlib,base64,json,os,uuid,subprocess,time,urllib.request
root=pathlib.Path(P['root']);assert root.resolve()==root
allowed=set(P['files']);assert allowed=={'frontend/control.js','frontend/tests/control.test.cjs','frontend/tests/corner-control.test.cjs','backend/tests/test_basic_line_integration.py','BASIC_LINE_FOLLOWING.md'}
sha=lambda b:hashlib.sha256(b).hexdigest()
def target(base,name):
 p=base/name;assert p.resolve()==p and base in p.parents;return p
def pid(unit):return int(subprocess.check_output(['systemctl','show',unit,'-p','MainPID','--value'],text=True))
op=urllib.request.build_opener(urllib.request.ProxyHandler({}))
def control():
 with op.open('http://127.0.0.1:8765/api/v1/control',timeout=3) as r:return json.load(r)['data']
def idle():
 c=control();assert not c['armed'] and c['outputs']==[0]*4 and not c['autonomy']['running'];return c
def owners():
 assert pid('rbe201tc-monitor.service')==P['pid'] and pid('rbe201tc-sensors.service')==P['sensor_pid']
 assert pathlib.Path('/proc/%d/cwd'%P['pid']).resolve()==root
for n,h in P['protected'].items():assert sha(target(root,n).read_bytes())==h,('Protected changed',n)
owners();c=idle();originals={n:target(root,n).read_bytes() for n in allowed}
for n,b in originals.items():assert sha(b)==P['before'][n],('Before changed',n)
stage=root.parent/('.speed84_stage_'+uuid.uuid4().hex);backup=root.parent/('.speed84_backup_'+uuid.uuid4().hex)
assert stage.resolve()==stage and backup.resolve()==backup;stage.mkdir();backup.mkdir()
for n,x in P['files'].items():
 b=base64.b64decode(x['content']);assert sha(b)==x['sha256'];p=target(stage,n);p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(b)
 bkp=target(backup,n);bkp.parent.mkdir(parents=True,exist_ok=True);bkp.write_bytes(originals[n])
env=dict(os.environ,PYTHONPATH=str(root/'backend'),PYTHONDONTWRITEBYTECODE='1')
r=subprocess.run(['/usr/bin/python3','-B','-m','unittest','discover','-s',str(stage/'backend/tests'),'-p','test_basic_line_integration.py','-v'],cwd=root/'backend',env=env,capture_output=True,text=True,timeout=45)
(stage/'integration_tests.log').write_text(r.stdout+r.stderr);assert r.returncode==0,(r.stdout+r.stderr)[-6000:]
owners();current=idle();assert current['drive_commands_sent']==c['drive_commands_sent']
installed=[]
try:
 for n in sorted(allowed):
  src=target(stage,n);dst=target(root,n);assert dst.read_bytes()==originals[n];os.replace(src,dst);installed.append(n)
 for n,x in P['files'].items():assert sha(target(root,n).read_bytes())==x['sha256']
 for n,h in P['protected'].items():assert sha(target(root,n).read_bytes())==h
 owners();after=idle();assert after['drive_commands_sent']==c['drive_commands_sent']
except Exception:
 for n in reversed(installed):
  dst=target(root,n);assert sha(dst.read_bytes())==P['files'][n]['sha256'];src=target(backup,n);assert src.read_bytes()==originals[n];os.replace(src,dst)
 raise
print(json.dumps({'result':'success','stage':str(stage),'backup':str(backup),'controller_pid_unchanged':P['pid'],'sampler_pid_unchanged':P['sensor_pid'],'motion_commands_sent_by_deployer':0,'process_restarts':0,'drive_count_before':c['drive_commands_sent'],'drive_count_after':after['drive_commands_sent'],'profile':'GUI autonomy_start.speed=84; backend compatibility default140 unchanged; manual200 unchanged','hashes':{n:x['sha256'] for n,x in P['files'].items()},'candidate_tests_tail':(r.stdout+r.stderr)[-900:]},ensure_ascii=False))
'''
program='import json\nP=json.loads('+repr(json.dumps(payload))+')\n'+REMOTE
compile(program,'speed_only_static_transaction','exec')
args=['ssh','-F','/Users/your-user/Library/Application Support/RBE201TC/connection/autoboat_ssh_config','-o','ClearAllForwardings=yes','-o','BatchMode=yes','-o','StrictHostKeyChecking=yes','-o','ConnectTimeout=4','rbe201tc-wifi','python3','-B','-']
r=subprocess.run(args,input=program,capture_output=True,text=True,timeout=65)
with (QA/'deployment.log').open('x') as f:f.write(r.stdout+r.stderr+'\n[exit code: %d]\n'%r.returncode)
print(r.stdout);print(r.stderr);print('[exit code: %d]'%r.returncode)
if r.returncode:raise SystemExit(r.returncode)
with (QA/'deployment_result.json').open('x') as f:json.dump(json.loads(r.stdout),f,ensure_ascii=False,indent=2)
