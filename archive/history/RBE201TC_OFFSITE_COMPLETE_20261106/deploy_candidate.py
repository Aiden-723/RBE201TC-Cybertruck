from pathlib import Path
import io,tarfile,subprocess,json,hashlib
root=Path('work/device-console-v2');out=Path('outputs/RBE201TC_OFFSITE_COMPLETE_20261106')
files=[p for p in root.rglob('*') if p.is_file() and '__pycache__' not in p.parts and p.suffix!='.pyc' and p.name!='.DS_Store']
b=io.BytesIO()
with tarfile.open(fileobj=b,mode='w:gz') as tar:
 for p in files:tar.add(p,arcname=p.relative_to(root).as_posix(),recursive=False)
ssh=['/usr/bin/ssh','-F','work/rdk_connection/autoboat_ssh_config','rbe201tc-wifi']
staging='/home/sunrise/rbe201tc-sensor-studio/diagnostics/offsite_complete_20261106'
subprocess.run(ssh+['mkdir -p '+staging],check=True)
subprocess.run(ssh+['cat > '+staging+'/candidate.tgz'],input=b.getvalue(),check=True)
script=r'''
from pathlib import Path,PurePosixPath
import tarfile,shutil,subprocess,json,urllib.request,hashlib,time,os
base=Path('/home/sunrise/rbe201tc-sensor-studio');staging=base/'diagnostics/offsite_complete_20261106'
old=(base/'readonly-current').resolve();candidate=base/'releases/offsite_complete_20261106'
assert not candidate.exists(),'Candidate already exists; do not overwrite'
shutil.copytree(old,candidate,ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
with tarfile.open(staging/'candidate.tgz') as tar:
 for m in tar.getmembers():
  path=PurePosixPath(m.name)
  assert m.isfile() and not path.is_absolute() and '..' not in path.parts
  p=candidate/path;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(tar.extractfile(m).read());p.chmod(m.mode & 0o777)
checks=[('backend_full.log',['python3','-B','-W','error','-m','unittest','discover','-s','tests','-v'],candidate/'backend'),('tools_full.log',['python3','-B','-W','error','-m','unittest','discover','-s','tools/tests','-v'],candidate)]
for name,command,cwd in checks:
 with (staging/name).open('w') as f:
  run=subprocess.run(command,cwd=cwd,stdout=f,stderr=subprocess.STDOUT)
 assert run.returncode==0,'Candidate tests failed: '+name
with (staging/'rehearsal.log').open('w') as f:
 run=subprocess.run(['python3','-B','tools/rehearse_mission.py','--output',str(staging/'simulation'),'--all'],cwd=candidate,stdout=f,stderr=subprocess.STDOUT)
assert run.returncode==0,'Rehearsal failed'
def get(path):return json.load(urllib.request.urlopen('http://127.0.0.1:8765/api/v1/'+path,timeout=3))['data']
control=get('control');acq=get('experiments')
assert control['enabled'] and not control['armed'] and control['outputs']==[0,0,0,0] and not control['fault'],'Controller not idle'
assert not acq['active_run_id'] and not acq['busy'],'Acquisition active'
assert hashlib.sha256((old/'backend/motor_control.py').read_bytes()).hexdigest()=='cc34dade1671c1ef33543368eaab04819a8054258ccf272f649d854a9100eed3','Unexpected motor change'
sensor_pid=subprocess.check_output(['systemctl','show','rbe201tc-sensors','-p','MainPID','--value'],text=True).strip()
def service(action):subprocess.run(['sudo','-n','systemctl',action,'rbe201tc-monitor'],check=True)
receipt={'old_release':str(old),'new_release':str(candidate),'before_drive_commands_sent':control['drive_commands_sent'],'sensor_pid_before':sensor_pid}
(staging/'before_activation.json').write_text(json.dumps(receipt,indent=2))
service('stop')
try:
 newlink=base/'.readonly-current-offsite';newlink.symlink_to(candidate);os.replace(newlink,base/'readonly-current')
 service('start')
 for attempt in range(24):
  try:
   control=get('control');break
  except Exception:time.sleep(.25)
 else:raise RuntimeError('Monitor did not recover')
 assert control['enabled'] and not control['fault'] and control['command_mode']=='speed'
 receipt.update(control={k:control.get(k) for k in ('enabled','armed','state','outputs','fault','command_mode','drive_commands_sent')},sensor_pid_after=subprocess.check_output(['systemctl','show','rbe201tc-sensors','-p','MainPID','--value'],text=True).strip(),source_sha256={p.relative_to(candidate).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in candidate.rglob('*') if p.is_file() and '__pycache__' not in p.parts},agent_nonzero_commands_sent=0)
 assert receipt['sensor_pid_after']==sensor_pid
 (staging/'activation.json').write_text(json.dumps(receipt,ensure_ascii=False,indent=2))
 print(json.dumps({k:v for k,v in receipt.items() if k!='source_sha256'},ensure_ascii=False))
except Exception:
 service('stop')
 link=base/'.readonly-current-rollback';link.symlink_to(old);os.replace(link,base/'readonly-current');service('start');raise
'''
r=subprocess.run(ssh+['python3 -'],input=script,text=True,capture_output=True)
(out/'deployment_stdout.log').write_text(r.stdout);(out/'deployment_stderr.log').write_text(r.stderr)
print(r.stdout);print(r.stderr)
raise SystemExit(r.returncode)
