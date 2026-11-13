from pathlib import Path
import json,hashlib,subprocess,urllib.request
OUT=Path(__file__).resolve().parent;ROOT=OUT.parents[1];P=ROOT/'work/device-console-v2';pre=json.loads((OUT/'production_before.json').read_text())
get=lambda path:urllib.request.urlopen('http://127.0.0.1:8765'+path,timeout=5).read()
c=json.loads(get('/api/v1/control'))['data'];js=get('/control.js')
# The user may already be testing; read state without taking control.
assert isinstance(c['speed_transition_resets'],int) and c['speed_transition_resets']>=0
assert c['command_mode']=='speed' and 'Motor_type:1' in c['serial_diagnostics']['configuration']
assert c['startup_boost_enabled'] is True and c['startup_boost_speed']==200 and c['startup_boost_duration_s']==.2
assert isinstance(c['startup_boost_active'],bool)
assert c['autonomy']['max_correction_speed']==50 and not c['autonomy']['direction_recovery_enabled']
assert c['autonomy']['white_correction_hold_limit_s']==.2
assert hashlib.sha256(js).hexdigest()==hashlib.sha256((P/'frontend/control.js').read_bytes()).hexdigest()
assert '每次动作先200/0.2秒' in js.decode() and '自动上限50' not in js.decode()
assert '短丢线续纠偏最多0.2秒' in js.decode() and '延续刚才纠偏' in js.decode()
code="""import pathlib,hashlib,json
root=pathlib.Path(%r)
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
print(json.dumps({'protected':{n:sha(root/n) for n in %r},'experiments':{str(p):sha(p) for p in pathlib.Path('/home/sunrise/rbe201tc-sensor-studio/experiments').rglob('*') if p.is_file()}}))
"""%(pre['root'],list(pre['protected']))
r=subprocess.run(['ssh','-F','/Users/your-user/Library/Application Support/RBE201TC/connection/autoboat_ssh_config','-o','ClearAllForwardings=yes','-o','BatchMode=yes','-o','ConnectTimeout=4','rbe201tc-wifi','python3','-B','-'],input=code,text=True,capture_output=True,check=True)
data=json.loads(r.stdout);assert data['protected']==pre['protected'];assert data['experiments']==pre['experiments']
data.update(speed_transition_resets=c['speed_transition_resets'],white_correction_hold_limit_s=.2,auto_cruise_target=50,startup_speed=200,startup_duration_s=.2,startup_boost_enabled=True,stopped=(not c['armed'] and c['outputs']==[0]*4),current_control={k:c.get(k) for k in ('state','armed','outputs','requested_outputs','startup_boost_active','speed_transition_resets','drive_commands_sent','driving_mode')},served_UI_verified=True,real_motion_sent=False)
(OUT/'final_verification.json').write_text(json.dumps(data,indent=2));print('Live code/flags/protected data verified; actual user-controlled state recorded without takeover.')
