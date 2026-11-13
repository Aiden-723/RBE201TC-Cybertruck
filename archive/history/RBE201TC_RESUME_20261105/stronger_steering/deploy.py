from pathlib import Path
import base64, json, subprocess
root=Path('work/device-console-v2')
files=['backend/motor_control.py','backend/tests/test_motor_control.py']
payload={name:base64.b64encode((root/name).read_bytes()).decode() for name in files}
script=r'''
from pathlib import Path
import base64,hashlib,json,subprocess,urllib.request,time,shutil
files=json.loads(PAYLOAD)
root=Path('/home/sunrise/rbe201tc-sensor-studio/readonly-current').resolve()
old=json.load(urllib.request.urlopen('http://127.0.0.1:8765/api/v1/control',timeout=4))['data']
assert old['outputs']==[0,0,0,0] and not old['armed'] and not old['fault'], 'Control is not idle'
assert hashlib.sha256((root/'backend/motor_control.py').read_bytes()).hexdigest()=='6e18547c91510898a7feac0d8a999dad1605e64457e9244e4d945e2e5709ba58','Remote source changed'
backup=root.parent.parent/('recovery-stronger-steering-'+time.strftime('%Y%m%dT%H%M%S'))
backup.mkdir()
for name in files:
    dest=backup/name;dest.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(root/name,dest)
def service(action):subprocess.run(['sudo','-n','systemctl',action,'rbe201tc-monitor.service'],check=True)
service('stop')
try:
    for name,data in files.items():
        p=root/name;tmp=p.with_suffix(p.suffix+'.new');tmp.write_bytes(base64.b64decode(data));tmp.replace(p)
    service('start')
    for attempt in range(20):
        try:
            state=json.load(urllib.request.urlopen('http://127.0.0.1:8765/api/v1/control',timeout=2))['data']
            break
        except Exception:time.sleep(.5)
    else:raise RuntimeError('Console did not recover')
    assert state['enabled'] and state['command_mode']=='speed' and not state['fault']
    print(json.dumps({'backup':str(backup),'control':{k:state.get(k) for k in ['enabled','state','armed','outputs','fault','command_mode','drive_commands_sent']},'sha256':{name:hashlib.sha256((root/name).read_bytes()).hexdigest() for name in files}}))
except Exception:
    service('stop')
    for name in files:shutil.copy2(backup/name,root/name)
    service('start')
    raise
'''.replace('PAYLOAD',repr(json.dumps(payload)))
r=subprocess.run(['/usr/bin/ssh','-F','work/rdk_connection/autoboat_ssh_config','rbe201tc-wifi','python3 -'],input=script,text=True,capture_output=True)
print(r.stdout);print(r.stderr)
if r.returncode:raise SystemExit(r.returncode)
Path('outputs/RBE201TC_RESUME_20261105/stronger_steering/deployment.json').write_text(r.stdout)
