from pathlib import Path
import base64,json,hashlib,subprocess
O=Path(__file__).resolve().parent;P=O.parents[1]/'work/device-console-v2'
files=['deploy/start_monitor.py','deploy/99-rbe201tc-usb-role.rules','deploy/rbe201tc-motor-reconnect.service','tools/tests/test_start_monitor.py','frontend/control.js','frontend/cockpit.css','frontend/tests/control.test.cjs','frontend/tests/corner-control.test.cjs']
old=json.loads((O.parent/'RBE201TC_OFFLINE_UI_RECOVERY_20261113/baseline.json').read_text());old['deploy/start_monitor.py']=hashlib.sha256((O/'start_monitor.before.py').read_bytes()).hexdigest()
protected=['backend/basic_line_follower.py','backend/driving_mode.py','backend/motor_control.py','backend/server.py','backend/hardware_worker.py','backend/line_trials.py','frontend/index.html','frontend/app.js','frontend/styles.css','frontend/vehicle.js']
payload={'files':{n:base64.b64encode((P/n).read_bytes()).decode() for n in files},'before':old,'protected':{n:hashlib.sha256((P/n).read_bytes()).hexdigest() for n in protected}}
remote=r'''
import pathlib,json,base64,hashlib,subprocess,urllib.request,time,uuid,os
root=pathlib.Path('/home/sunrise/rbe201tc-sensor-studio/releases/cockpit_driving_20261106')
assert root.resolve()==root
sha=lambda b:hashlib.sha256(b).hexdigest()
def run(args):return subprocess.run(args,text=True,capture_output=True,check=True,timeout=30)
def get(name):return json.load(urllib.request.urlopen('http://127.0.0.1:8765/api/v1/'+name,timeout=3))['data']
def pid(name):return int(run(['systemctl','show',name,'-p','MainPID','--value']).stdout)
c=get('control');ex=get('experiments');assert not c['armed'] and c['outputs']==[0]*4 and not c['autonomy']['running']
assert not ex.get('active_run_id') and not any(ex.get(k) for k in ('busy','committing','closing'))
for n,h in P['before'].items():assert sha((root/n).read_bytes())==h,('Unexpected source',n)
for n,h in P['protected'].items():assert sha((root/n).read_bytes())==h,('Protected source',n)
ports={}
for tty in pathlib.Path('/dev').glob('ttyUSB*'):
 props={l.split('=',1)[0]:l.split('=',1)[1] for l in run(['udevadm','info','-q','property','-n',str(tty)]).stdout.splitlines() if '=' in l}
 if props.get('ID_VENDOR_ID')=='1a86':ports.setdefault(props.get('ID_MODEL_ID'),[]).append(str(tty))
assert len(ports.get('7522',[]))==1 and len(ports.get('7523',[]))==1,ports
motor,imu=ports['7522'][0],ports['7523'][0]
oldpid=pid('rbe201tc-monitor.service');sensorpid=pid('rbe201tc-sensors.service')
backup=root.parent/('.usb_role_backup_'+uuid.uuid4().hex);backup.mkdir(mode=0o700)
blobs={n:base64.b64decode(b) for n,b in P['files'].items()}
for n,b in blobs.items():
 p=root/n
 if p.exists():
  q=backup/n;q.parent.mkdir(parents=True,exist_ok=True);q.write_bytes(p.read_bytes())
 else:assert n not in P['before'],n
for path in ['/etc/udev/rules.d/99-rbe201tc-usb-role.rules','/etc/systemd/system/rbe201tc-motor-reconnect.service']:
 assert not pathlib.Path(path).exists(),('Existing config requires review',path)
for n,b in blobs.items():
 dst=root/n;dst.parent.mkdir(parents=True,exist_ok=True);tmp=dst.with_name(dst.name+'.usb-fix');tmp.write_bytes(b);os.replace(tmp,dst)
run(['systemd-analyze','verify',str(root/'deploy/rbe201tc-motor-reconnect.service')])
run(['sudo','-n','install','-m','644',str(root/'deploy/99-rbe201tc-usb-role.rules'),'/etc/udev/rules.d/99-rbe201tc-usb-role.rules'])
run(['sudo','-n','install','-m','644',str(root/'deploy/rbe201tc-motor-reconnect.service'),'/etc/systemd/system/rbe201tc-motor-reconnect.service'])
run(['sudo','-n','systemctl','daemon-reload']);run(['sudo','-n','udevadm','control','--reload-rules'])
# A change event creates aliases without deliberately emulating a new plug-in.
for port in [imu,motor]:run(['sudo','-n','udevadm','trigger','--action=change','--subsystem-match=tty','--sysname-match='+pathlib.Path(port).name])
run(['sudo','-n','udevadm','settle','--timeout=5'])
assert pathlib.Path('/dev/rbe201tc-motor').resolve()==pathlib.Path(motor)
assert pathlib.Path('/dev/rbe201tc-imu').resolve()==pathlib.Path(imu)
run(['sudo','-n','systemctl','restart','rbe201tc-monitor.service'])
end=time.monotonic()+12
while True:
 try:
  c=get('control')
  assert c['enabled'] and not c['armed'] and c['outputs']==[0]*4 and not c['fault']
  assert 'Motor_Version:1.7.3' in json.dumps(c.get('serial_diagnostics',{}))
  assert 'read_flash:OK!' in json.dumps(c.get('serial_diagnostics',{}))
  break
 except (OSError,ValueError,AssertionError):
  if time.monotonic()>end:raise
  time.sleep(.2)
newpid=pid('rbe201tc-monitor.service');assert newpid!=oldpid and pid('rbe201tc-sensors.service')==sensorpid
argv=pathlib.Path('/proc/%d/cmdline'%newpid).read_bytes().decode().split('\0')
assert '/dev/rbe201tc-motor' in argv,argv
for n,h in P['protected'].items():assert sha((root/n).read_bytes())==h,n
for n,b in blobs.items():assert (root/n).read_bytes()==b,n
result={'result':'success','backup':str(backup),'old_pid':oldpid,'new_pid':newpid,'sensor_pid':sensorpid,'motor_device':motor,'imu_device':imu,'motor_alias':'/dev/rbe201tc-motor','imu_alias':'/dev/rbe201tc-imu','serial_diagnostics':c['serial_diagnostics'],'control':{k:c.get(k) for k in ['enabled','armed','state','fault','outputs','drive_commands_sent']},'protected_hashes':P['protected'],'installed_hashes':{n:sha(b) for n,b in blobs.items()},'hotplug_rule_installed':True,'physical_replug_tested':False,'nonzero_motion_sent_by_assistant':False}
(backup/'result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2));print(json.dumps(result,ensure_ascii=False))
'''
program='import json\nP=json.loads('+repr(json.dumps(payload))+')\n'+remote
(O/'payload_sha256.txt').write_text(hashlib.sha256(program.encode()).hexdigest()+'\n')
r=subprocess.run(['ssh','-F','/Users/your-user/Library/Application Support/RBE201TC/connection/autoboat_ssh_config','-o','ClearAllForwardings=yes','-o','BatchMode=yes','-o','ConnectTimeout=4','rbe201tc-wifi','python3','-B','-'],input=program,text=True,capture_output=True,timeout=90)
(O/'apply.log').write_text(r.stdout+r.stderr+'\nexit='+str(r.returncode));print(r.stdout);print(r.stderr);assert r.returncode==0
(O/'result.json').write_text(json.dumps(json.loads(r.stdout),ensure_ascii=False,indent=2))
