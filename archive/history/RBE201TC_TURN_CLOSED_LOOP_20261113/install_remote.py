
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
for n,b in blobs.items():
 if n.endswith('.py'):compile(b,n,'exec')
oldpid=pid('rbe201tc-monitor.service');sensorpid=pid('rbe201tc-sensors.service')
backup=root.parent/('.angle_closed_loop_backup_'+uuid.uuid4().hex);backup.mkdir(mode=0o700)
for n in blobs:
 f=backup/n;f.parent.mkdir(parents=True,exist_ok=True);f.write_bytes((root/n).read_bytes())
try:
 for n,b in blobs.items():
  dst=root/n;tmp=dst.with_name(dst.name+'.angle-closed-loop');tmp.write_bytes(b);os.replace(tmp,dst)
 test=subprocess.run(['python3','-B','-m','unittest','discover','-s','tests'],cwd=root/'backend',text=True,capture_output=True,timeout=45)
 (backup/'tests.log').write_text(test.stdout+test.stderr);assert test.returncode==0,test.stdout+test.stderr
 idle();run(['sudo','-n','systemctl','restart','rbe201tc-monitor.service'])
 deadline=time.monotonic()+12
 while True:
  try:
   c=get('control');imu=get('imu');a=c['autonomy']
   assert c['enabled'] and not c['armed'] and c['outputs']==[0]*4 and not c['fault']
   assert a['control_policy']=='line_straight_imu_quarter_turn_v4'
   assert a['angle_tolerance_deg']==2 and a['white_confirmation_samples']==2 and a['turn_index']==0
   assert imu['status']=='live' and not imu['calibrated']
   break
  except (OSError,ValueError,AssertionError):
   if time.monotonic()>deadline:raise
   time.sleep(.15)
 for n,h in P['protected'].items():assert sha((root/n).read_bytes())==h,n
 for n,b in blobs.items():assert (root/n).read_bytes()==b,n
 assert pid('rbe201tc-sensors.service')==sensorpid
 result={'result':'success','backup':str(backup),'old_monitor_pid':oldpid,'new_monitor_pid':pid('rbe201tc-monitor.service'),'sensor_pid_unchanged':sensorpid,'control':{k:c.get(k) for k in ['state','armed','outputs','fault','drive_commands_sent']},'autonomy':{k:a.get(k) for k in ['control_policy','phase','turn_index','angle_stage','angle_tolerance_deg','white_confirmation_samples','white_samples','reason']},'imu':imu,'tests':test.stdout+test.stderr,'protected_hashes':P['protected'],'installed_hashes':{n:sha(b) for n,b in blobs.items()},'motion_sent_by_assistant':False,'calibration_sent_by_assistant':False}
 (backup/'result.json').write_text(json.dumps(result,indent=2));print(json.dumps(result))
except Exception:
 for n in blobs:(root/n).write_bytes((backup/n).read_bytes())
 run(['sudo','-n','systemctl','restart','rbe201tc-monitor.service']);raise
