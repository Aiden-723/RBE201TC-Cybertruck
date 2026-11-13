"""Explicit single-use guarded deployment to the observed robot release.

No control POSTs or hardware tests. Candidate tests use fake transports only.
The original production files are backed up and restored on a failed launch.
"""
from pathlib import Path
import argparse
import base64
import hashlib
import json
import subprocess

ROOT = Path(__file__).resolve().parents[3]
PROJECT = ROOT / 'work/device-console-v2'
QA = Path(__file__).resolve().parent
FILES = ['backend/basic_line_follower.py', 'backend/motor_control.py',
         'backend/driving_mode.py', 'backend/server.py', 'frontend/control.js',
         'backend/tests/test_basic_line_follower.py', 'backend/tests/test_autonomous_motor_bridge.py',
         'backend/tests/test_basic_line_integration.py', 'frontend/tests/control.test.cjs',
         'BASIC_LINE_FOLLOWING.md']
REMOTE = r'''
import ast,base64,hashlib,json,os,pathlib,re,shutil,signal,subprocess,time,urllib.request,uuid
root=pathlib.Path(PAYLOAD['root']); assert root.resolve()==root and root.is_dir()
assert root==pathlib.Path('/home/sunrise/rbe201tc-sensor-studio/releases/cockpit_driving_20261106')
allowed=set(PAYLOAD['files']); assert len(allowed)==10 and all(not pathlib.Path(n).is_absolute() and '..' not in pathlib.Path(n).parts for n in allowed)
sha=lambda b:hashlib.sha256(b).hexdigest()
def target(parent,name):
 p=parent/name; assert p.resolve()==parent/name and not p.is_symlink(),str(p); return p
opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
def get(path):
 with opener.open('http://127.0.0.1:8765'+path,timeout=2) as r:return json.load(r)['data']
def stopped():
 c=get('/api/v1/control');a=get('/api/v1/experiments')
 assert c.get('armed') is False and c.get('outputs')==[0]*4 and not c.get('fault'),'Control is not safely disarmed'
 assert not a.get('active_run_id') and not a.get('busy') and not a.get('committing'),'Capture active'
 return c,a
def listener():
 s=subprocess.run(['ss','-ltnp','sport = :8765'],capture_output=True,text=True,check=True).stdout
 ids=set(re.findall(r'pid=(\d+)',s));assert len(ids)==1,ids
 pid=int(next(iter(ids)));assert pathlib.Path('/proc/%d/cwd'%pid).resolve()==root
 return pid
pid=listener();assert pid==PAYLOAD['pid'],'Production PID changed; do not replay'
stopped()
for name,wanted in PAYLOAD['before'].items():assert sha(target(root,name).read_bytes())==wanted,('Production changed',name)
release_base=root.parent;stage=release_base/('.basic_line_stage_'+uuid.uuid4().hex)
assert stage.resolve()==stage and not stage.exists();stage.mkdir(mode=0o700)
for directory in ('backend','frontend','tools','config'):
 src=target(root,directory);assert src.is_dir();shutil.copytree(src,stage/directory,ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
blobs={name:base64.b64decode(info['content'],validate=True) for name,info in PAYLOAD['files'].items()}
for name,data in blobs.items():
 assert sha(data)==PAYLOAD['files'][name]['sha256'],name
 if name.endswith('.py'):ast.parse(data.decode('utf-8'),filename=name)
 p=target(stage,name);p.parent.mkdir(parents=True,exist_ok=True)
 if p.exists():p.read_bytes()  # Observe the copied file before replacement.
 p.write_bytes(data)
tests=subprocess.run(['/usr/bin/python3','-B','-m','unittest','discover','-s','tests','-v'],cwd=stage/'backend',capture_output=True,text=True,timeout=90,env={**os.environ,'PYTHONDONTWRITEBYTECODE':'1'})
(stage/'candidate_backend_tests.log').write_text(tests.stdout+tests.stderr+'\n[exit code: %d]\n'%tests.returncode)
assert tests.returncode==0,('Candidate tests failed; live source unchanged',str(stage/'candidate_backend_tests.log'),(tests.stdout+tests.stderr)[-3000:])
# Re-check after candidate tests and before touching the sole live owner.
assert listener()==pid;stopped()
for name,wanted in PAYLOAD['before'].items():assert sha(target(root,name).read_bytes())==wanted,('Production changed during staging',name)
backup=release_base/('.basic_line_backup_'+uuid.uuid4().hex);assert backup.resolve()==backup and not backup.exists();backup.mkdir(mode=0o700)
originals={}
for name in allowed:
 p=target(root,name);originals[name]=p.read_bytes() if p.exists() else None
 if originals[name] is not None:
  b=target(backup,name);b.parent.mkdir(parents=True,exist_ok=True);b.write_bytes(originals[name])
argv=PAYLOAD['argv'];assert argv[0]=='/usr/bin/python3' and argv[1:4]==['-B','-u','backend/server.py']
assert '--motor-mode' in argv and argv[argv.index('--motor-mode')+1]=='speed'
journal={'stage':str(stage),'backup':str(backup),'old_pid':pid,'root':str(root),'motion_commands_sent_by_deployer':0,'candidate_tests_exit':tests.returncode,'candidate_tests_tail':(tests.stdout+tests.stderr)[-400:]}
def write_journal():
 (backup/'deployment_journal.json').write_text(json.dumps(journal,ensure_ascii=False,indent=2))
write_journal()
def launch(label):
 log=(backup/(label+'.log')).open('ab')
 try:return subprocess.Popen(argv,cwd=root,stdin=subprocess.DEVNULL,stdout=log,stderr=subprocess.STDOUT,start_new_session=True,env={**os.environ,'PYTHONDONTWRITEBYTECODE':'1'})
 finally:log.close()
def wait_healthy(process,expect_auto):
 end=time.monotonic()+12
 while time.monotonic()<end:
  if process.poll() is not None:raise RuntimeError('Server exited: '+str(process.returncode))
  try:
   c,a=stopped();s=get('/api/v1/status')
   assert listener()==process.pid
   assert c.get('driving_mode')=='manual' and c.get('drive_commands_sent')==0
   assert c.get('autonomy',{}).get('ready') is expect_auto and not c.get('autonomy',{}).get('running',False)
   assert s['sensors']['line_sensors']['status']=='live'
   return c,a,s
  except (OSError,ValueError,KeyError,AssertionError):time.sleep(.15)
 raise RuntimeError('New service not healthy within deadline')
new=None;installed=[]
try:
 assert pathlib.Path('/proc/%d/cwd'%pid).resolve()==root
 os.kill(pid,signal.SIGTERM)
 end=time.monotonic()+8
 while pathlib.Path('/proc/%d'%pid).exists() and time.monotonic()<end:time.sleep(.1)
 assert not pathlib.Path('/proc/%d'%pid).exists(),'Old process still owns production; no files installed'
 for name in sorted(allowed):
  src=target(stage,name);dst=target(root,name);dst.parent.mkdir(parents=True,exist_ok=True)
  assert sha(src.read_bytes())==PAYLOAD['files'][name]['sha256']
  current=dst.read_bytes() if dst.exists() else None;assert current==originals[name],('Concurrent source change',name)
  # Both resolved absolute paths were verified against fixed allowlisted roots.
  os.replace(src,dst);installed.append(name)
 new=launch('new_server');journal['new_pid']=new.pid;write_journal()
 c,a,s=wait_healthy(new,True)
 for name,info in PAYLOAD['files'].items():assert sha(target(root,name).read_bytes())==info['sha256'],name
 for name,wanted in PAYLOAD['protected'].items():assert sha(target(root,name).read_bytes())==wanted,('Protected changed',name)
 journal.update(result='success',control={k:c.get(k) for k in ('state','armed','outputs','fault','driving_mode','drive_commands_sent','autonomy')},experiment_run_count=len(a.get('runs',[])),sensor_status={k:s['sensors'][k].get('status') for k in ('line_sensors','camera','dht11')},hashes={n:info['sha256'] for n,info in PAYLOAD['files'].items()})
 write_journal();print(json.dumps(journal,ensure_ascii=False))
except Exception as exc:
 journal.update(result='failed',error=type(exc).__name__+': '+str(exc));write_journal()
 if new is not None and new.poll() is None:
  assert pathlib.Path('/proc/%d/cwd'%new.pid).resolve()==root
  new.terminate();new.wait(timeout=8)
 for name in reversed(installed):
  p=target(root,name);assert sha(p.read_bytes())==PAYLOAD['files'][name]['sha256'],('Foreign change blocks rollback',name)
  if originals[name] is None:p.unlink()  # Only the verified, newly installed allowlisted file.
  else:
   b=target(backup,name);assert b.read_bytes()==originals[name]
   restore=target(backup,name+'.restore');restore.write_bytes(originals[name]);os.replace(restore,p)
 if not pathlib.Path('/proc/%d'%pid).exists():
  old=launch('rollback_server');journal['rollback_pid']=old.pid
  try:wait_healthy(old,False);journal['rollback']='healthy'
  except Exception as recovery:journal['rollback']='inspect:'+str(recovery)
 write_journal();print(json.dumps(journal,ensure_ascii=False));raise
'''


def main():
    cli=argparse.ArgumentParser(description=__doc__)
    cli.add_argument('--apply',action='store_true',required=True)
    cli.parse_args()
    baseline=json.loads((QA.parent/'source_baseline.json').read_text())
    pre=json.loads((QA/'robot_preflight.json').read_text())
    critical=['backend/motor_control.py','backend/driving_mode.py','backend/server.py','frontend/control.js']
    for name in critical:
        assert pre['hashes'][name]==baseline['before'][name]['sha256'], ('Unexpected live version',name)
    protected={name:pre['hashes'][name] for name in ('backend/hardware_worker.py','backend/readings.py','frontend/app.js','frontend/index.html')}
    payload={'root':pre['cwd'],'pid':pre['pid'],'argv':pre['argv'],
             'before':{name:pre['hashes'][name] for name in critical},'protected':protected,
             'files':{name:{'content':base64.b64encode((PROJECT/name).read_bytes()).decode(),
                            'sha256':hashlib.sha256((PROJECT/name).read_bytes()).hexdigest()} for name in FILES}}
    program='import json\nPAYLOAD=json.loads('+repr(json.dumps(payload))+')\n'+REMOTE
    compile(program,'guarded_robot_deployment','exec')
    args=['ssh','-F','/Users/your-user/Library/Application Support/RBE201TC/connection/autoboat_ssh_config',
          '-o','ClearAllForwardings=yes','-o','BatchMode=yes','-o','StrictHostKeyChecking=yes',
          '-o','ConnectTimeout=4','rbe201tc-wifi','python3','-B','-']
    result=subprocess.run(args,input=program,capture_output=True,text=True,timeout=150)
    with (QA/'robot_deployment.log').open('x') as f:
        f.write(result.stdout+result.stderr+'\n[exit code: '+str(result.returncode)+']\n')
    print(result.stdout);print(result.stderr);print('[exit code: %d]'%result.returncode)
    if result.returncode:raise SystemExit(result.returncode)
    report=json.loads(result.stdout)
    with (QA/'robot_deployment_result.json').open('x') as f:json.dump(report,f,ensure_ascii=False,indent=2)


if __name__=='__main__':
    main()
