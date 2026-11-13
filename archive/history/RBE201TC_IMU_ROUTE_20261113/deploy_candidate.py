from pathlib import Path
import base64, hashlib, json, subprocess
O=Path(__file__).resolve().parent
P=O.parents[1]/'work/device-console-v2'
modified=['backend/basic_line_follower.py','backend/driving_mode.py','backend/server.py','frontend/index.html','frontend/control.js','frontend/cockpit.css','deploy/start_monitor.py']
new=['backend/imu_heading.py','backend/imu_line_follower.py','frontend/heading.js','deploy/imu_route.json','backend/tests/test_imu_heading.py','backend/tests/test_imu_line_follower.py','frontend/tests/heading.test.cjs']
protected=['backend/motor_control.py','backend/hardware_worker.py','backend/line_trials.py','frontend/app.js','frontend/styles.css','frontend/vehicle.js','frontend/cybertruck-photo.jpg','frontend/acquisition.js','frontend/trials.js']
sha=lambda b:hashlib.sha256(b).hexdigest()
payload={'files':{n:base64.b64encode((P/n).read_bytes()).decode() for n in modified+new},'before':{n:sha((O/'before'/n).read_bytes()) for n in modified},'protected':{n:sha((P/n).read_bytes()) for n in protected}}
(O/'candidate_manifest.json').write_text(json.dumps({n:sha((P/n).read_bytes()) for n in modified+new},indent=2))
remote=r'''
import pathlib,json,base64,hashlib,subprocess,urllib.request,time,uuid,os
root=pathlib.Path('/home/sunrise/rbe201tc-sensor-studio/releases/cockpit_driving_20261106')
sha=lambda b:hashlib.sha256(b).hexdigest()
def run(args,timeout=30):return subprocess.run(args,text=True,capture_output=True,check=True,timeout=timeout)
def get(name):return json.load(urllib.request.urlopen('http://127.0.0.1:8765/api/v1/'+name,timeout=3))['data']
def pid(name):return int(run(['systemctl','show',name,'-p','MainPID','--value']).stdout)
c=get('control');ex=get('experiments')
assert not c['armed'] and c['outputs']==[0]*4 and not c['autonomy']['running'],'Robot is in use'
assert not ex.get('active_run_id') and not any(ex.get(k) for k in ('busy','committing','closing')),'Acquisition in use'
for n,h in P['before'].items():assert sha((root/n).read_bytes())==h,('Source changed',n)
for n,h in P['protected'].items():assert sha((root/n).read_bytes())==h,('Protected source changed',n)
blobs={n:base64.b64decode(b) for n,b in P['files'].items()}
for n,b in blobs.items():
 if n.endswith('.py'):compile(b,n,'exec')
for n in set(blobs)-set(P['before']):assert not (root/n).exists(),('New path already exists',n)
oldpid=pid('rbe201tc-monitor.service');sensorpid=pid('rbe201tc-sensors.service')
backup=root.parent/('.imu_route_backup_'+uuid.uuid4().hex);backup.mkdir(mode=0o700)
for n in blobs:
 if (root/n).exists():
  p=backup/n;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes((root/n).read_bytes())
(backup/'new_files.json').write_text(json.dumps(sorted(set(blobs)-set(P['before']))))
try:
 for n,b in blobs.items():
  p=root/n;p.parent.mkdir(parents=True,exist_ok=True);tmp=p.with_name(p.name+'.imu-candidate');tmp.write_bytes(b);os.replace(tmp,p)
 test=run(['python3','-B','-m','unittest','discover','-s','tests','-p','test_imu*.py'],timeout=30) if False else subprocess.run(['python3','-B','-m','unittest','discover','-s','tests','-p','test_imu*.py'],cwd=root/'backend',text=True,capture_output=True,timeout=30)
 (backup/'tests.log').write_text(test.stdout+test.stderr);assert test.returncode==0,test.stdout+test.stderr
 # Recheck immediately before the service restart.
 c=get('control');assert not c['armed'] and not c['autonomy']['running'] and c['outputs']==[0]*4,'Robot became active'
 run(['sudo','-n','systemctl','restart','rbe201tc-monitor.service'])
 deadline=time.monotonic()+12
 while True:
  try:
   c=get('control');imu=get('imu')
   assert c['enabled'] and not c['armed'] and c['outputs']==[0]*4 and not c['fault']
   assert c['autonomy']['control_policy']=='line_straight_imu_quarter_turn_v1'
   assert c['autonomy']['turn_count']==14 and c['autonomy']['turn_index']==0
   assert imu['status']=='live' and not imu['calibrated']
   break
  except (OSError,ValueError,AssertionError):
   if time.monotonic()>deadline:raise
   time.sleep(.15)
 first=imu;time.sleep(.5);last=get('imu');assert last['successes']>first['successes']
 assert pid('rbe201tc-sensors.service')==sensorpid
 for n,h in P['protected'].items():assert sha((root/n).read_bytes())==h,n
 for n,b in blobs.items():assert (root/n).read_bytes()==b,n
 result={'result':'success','backup':str(backup),'old_monitor_pid':oldpid,'new_monitor_pid':pid('rbe201tc-monitor.service'),'sensor_pid_unchanged':sensorpid,'imu_first':first,'imu_last':last,'control':{k:c.get(k) for k in ['enabled','armed','state','outputs','fault','drive_commands_sent']},'policy':c['autonomy']['control_policy'],'turn_count':c['autonomy']['turn_count'],'route':json.loads(blobs['deploy/imu_route.json']),'protected_hashes':P['protected'],'installed_hashes':{n:sha(b) for n,b in blobs.items()},'tests':test.stdout+test.stderr,'nonzero_motion_sent_by_assistant':False,'calibration_sent_by_assistant':False}
 (backup/'result.json').write_text(json.dumps(result,indent=2));print(json.dumps(result))
except Exception:
 for n in blobs:
  p=root/n;old=backup/n
  if old.exists():p.write_bytes(old.read_bytes())
  elif p.exists():p.unlink()
 run(['sudo','-n','systemctl','restart','rbe201tc-monitor.service'])
 raise
'''
program='import json\nP=json.loads('+repr(json.dumps(payload))+')\n'+remote
r=subprocess.run(['ssh','-F','/Users/your-user/Library/Application Support/RBE201TC/connection/autoboat_ssh_config','-o','ClearAllForwardings=yes','-o','BatchMode=yes','-o','ConnectTimeout=4','rbe201tc-wifi','python3','-B','-'],input=program,text=True,capture_output=True,timeout=80)
(O/'deployment.log').write_text(r.stdout+r.stderr+'\nexit='+str(r.returncode))
if r.returncode:
 print(r.stderr);raise SystemExit(r.returncode)
x=json.loads(r.stdout);(O/'deployment.json').write_text(json.dumps(x,indent=2));print(json.dumps({k:v for k,v in x.items() if k not in ['protected_hashes','installed_hashes']},indent=2))
