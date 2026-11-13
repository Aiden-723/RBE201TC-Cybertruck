
import json,base64,pathlib,hashlib,subprocess,urllib.request,uuid,os,time
root=pathlib.Path('/home/sunrise/rbe201tc-sensor-studio/releases/cockpit_driving_20261106')
sha=lambda b:hashlib.sha256(b).hexdigest()
def run(args):return subprocess.run(args,text=True,capture_output=True,timeout=30,check=True)
def get(name):return json.load(urllib.request.urlopen('http://127.0.0.1:8765/api/v1/'+name,timeout=4))['data']
def pid(name):return int(run(['systemctl','show',name,'-p','MainPID','--value']).stdout)
def idle():
 c=get('control');assert not c['armed'] and not c['autonomy']['running'] and c['outputs']==[0]*4,'Robot in use'
idle();ex=get('experiments');assert not ex.get('active_run_id') and not any(ex.get(k) for k in ('busy','committing','closing'))
for n,h in {**P['before'],**P['protected']}.items():assert sha((root/n).read_bytes())==h,('Source mismatch',n)
blobs={n:base64.b64decode(b) for n,b in P['files'].items()}
new=set(blobs)-set(P['before'])
for n in new:assert not (root/n).exists(),('New path exists',n)
for n,b in blobs.items():
 if n.endswith('.py'):compile(b,n,'exec')
oldpid=pid('rbe201tc-monitor.service');sensorpid=pid('rbe201tc-sensors.service')
backup=root.parent/('.positive_arc_backup_'+uuid.uuid4().hex);backup.mkdir(mode=0o700)
for n in P['before']:
 f=backup/n;f.parent.mkdir(parents=True,exist_ok=True);f.write_bytes((root/n).read_bytes())
(backup/'new_files.json').write_text(json.dumps(sorted(new)))
try:
 for n,b in blobs.items():
  dst=root/n;dst.parent.mkdir(parents=True,exist_ok=True);tmp=dst.with_name(dst.name+'.positive-arc');tmp.write_bytes(b);os.replace(tmp,dst)
 test=subprocess.run(['python3','-B','-m','unittest','test_motor_control','test_track_imu_follower','test_mission_runtime'],cwd=root/'backend',env={**os.environ,'PYTHONPATH':str(root/'backend')+':'+str(root/'backend/tests')},text=True,capture_output=True,timeout=120)
 (backup/'tests.log').write_text(test.stdout+test.stderr);assert test.returncode==0,test.stdout+test.stderr
 idle();run(['sudo','-n','systemctl','restart','rbe201tc-monitor.service'])
 deadline=time.monotonic()+12
 while True:
  try:
   c=get('control');imu=get('imu');a=c['autonomy']
   assert c['enabled'] and not c['armed'] and c['outputs']==[0]*4 and not c['fault']
   assert a['control_policy']=='line_positive_arc_imu_turn_v4' and a['imu_required'] is True
   assert not a['ready'] and a['reason']=='imu_not_calibrated' and a['white_confirmation_samples']==10 and a['turn_index']==0
   assert a['speed_gears']==[20,30,40] and a['minimum_turn_output']==40
   assert a['reverse_seconds']==.8 and a['correction_outer']==200 and a['correction_inner']==40 and a['max_correction_speed']==200
   assert imu['status']=='live' and not imu['calibrated']
   break
  except (OSError,ValueError,AssertionError):
   if time.monotonic()>deadline:raise
   time.sleep(.15)
 for n,h in P['protected'].items():assert sha((root/n).read_bytes())==h,n
 for n,b in blobs.items():assert (root/n).read_bytes()==b,n
 assert pid('rbe201tc-sensors.service')==sensorpid
 result={'result':'success','backup':str(backup),'old_monitor_pid':oldpid,'new_monitor_pid':pid('rbe201tc-monitor.service'),'sensor_pid_unchanged':sensorpid,'control':{k:c.get(k) for k in ['state','armed','outputs','fault','drive_commands_sent']},'autonomy':{k:a.get(k) for k in ['ready','running','control_policy','phase','turn_index','turn_count','imu_required','white_confirmation_samples','reason','speed_gears','minimum_running_output','minimum_turn_output','reverse_seconds','correction_outer','correction_inner','max_correction_speed']},'imu':imu,'tests':test.stdout+test.stderr,'protected_hashes':P['protected'],'installed_hashes':{n:sha(b) for n,b in blobs.items()},'motion_sent_by_assistant':False,'imu_configuration_changed':False,'imu_hardware_calibration_sent':False}
 (backup/'result.json').write_text(json.dumps(result,indent=2));print(json.dumps(result))
except Exception:
 for n in blobs:
  if n in new:
   if (root/n).exists():(root/n).unlink()
  else:(root/n).write_bytes((backup/n).read_bytes())
 run(['sudo','-n','systemctl','restart','rbe201tc-monitor.service']);raise
