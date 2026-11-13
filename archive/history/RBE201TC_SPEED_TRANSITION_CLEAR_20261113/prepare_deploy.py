from pathlib import Path
import json,hashlib,subprocess
OUT=Path(__file__).resolve().parent;ROOT=OUT.parents[1];P=ROOT/'work/device-console-v2'
files=[str(p.relative_to(OUT/'before')) for p in (OUT/'before').rglob('*') if p.is_file()]
protected=['backend/server.py','frontend/control.js','frontend/tests/corner-control.test.cjs','frontend/index.html','deploy/start_monitor.py','config/hardware_observations.json','backend/driving_mode.py','backend/hardware_worker.py','backend/readings.py','frontend/app.js','frontend/cockpit.css','frontend/vehicle.js','frontend/acquisition.js','backend/basic_line_follower.py','backend/tests/test_corner_line_follower.py','backend/tests/test_basic_line_integration.py']
code='''import pathlib,json,hashlib,subprocess,urllib.request
root=pathlib.Path('/home/sunrise/rbe201tc-sensor-studio/releases/cockpit_driving_20261106')
get=lambda n:json.load(urllib.request.urlopen('http://127.0.0.1:8765/api/v1/'+n,timeout=4))['data']
c=get('control');a=get('experiments')
assert not c['armed'] and c['outputs']==[0]*4 and not c['fault'] and not c['autonomy']['running']
assert not a.get('active_run_id') and not any(a.get(k) for k in ('busy','committing','closing'))
pid=int(subprocess.check_output(['systemctl','show','rbe201tc-monitor.service','-p','MainPID','--value'],text=True));sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
print(json.dumps({'root':str(root),'pid':pid,'argv':pathlib.Path('/proc/%%d/cmdline'%%pid).read_bytes().decode().strip('\\0').split('\\0'),'sensor_service_pid':int(subprocess.check_output(['systemctl','show','rbe201tc-sensors.service','-p','MainPID','--value'],text=True)),'before':{n:{'sha256':sha(root/n)} for n in %r},'protected':{n:sha(root/n) for n in %r},'experiments':{str(p):sha(p) for p in pathlib.Path('/home/sunrise/rbe201tc-sensor-studio/experiments').rglob('*') if p.is_file()},'last_control':{k:v for k,v in c.items() if k not in ['control_key','session','autonomy_session']}}))
'''%(files,protected)
r=subprocess.run(['ssh','-F','/Users/your-user/Library/Application Support/RBE201TC/connection/autoboat_ssh_config','-o','ClearAllForwardings=yes','-o','ConnectTimeout=4','rbe201tc-wifi','python3','-B','-'],input=code,text=True,capture_output=True,check=True);b=json.loads(r.stdout)
for n,h in b['before'].items():assert hashlib.sha256((OUT/'before'/n).read_bytes()).hexdigest()==h['sha256'],n
for n,h in b['protected'].items():assert hashlib.sha256((P/n).read_bytes()).hexdigest()==h,n
(OUT/'production_before.json').write_text(json.dumps(b,ensure_ascii=False,indent=2))
