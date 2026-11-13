from pathlib import Path
import json,hashlib,subprocess,urllib.request
OUT=Path(__file__).resolve().parent;ROOT=OUT.parents[1];P=ROOT/'work/device-console-v2';pre=json.loads((OUT/'production_before.json').read_text())
get=lambda path:urllib.request.urlopen('http://127.0.0.1:8765'+path,timeout=5).read()
c=json.loads(get('/api/v1/control'))['data'];js=get('/control.js')
assert not c['armed'] and c['outputs']==[0]*4 and not c['autonomy']['running']
assert c['command_mode']=='speed'
assert 'Motor_type:1' in c['serial_diagnostics']['configuration']
assert c['autonomy']['max_correction_speed']==50 and c['autonomy']['direction_recovery_enabled'] is False
assert c['autonomy']['line_loss_limit_s']==1.4 and c['autonomy']['period_s']==.02
assert hashlib.sha256(js).hexdigest()==hashlib.sha256((P/'frontend/control.js').read_bytes()).hexdigest()
assert '两线TT动力控制' not in js.decode() and '丢线即停' in js.decode() and 'line_gap_search_right' not in js.decode() and "autonomy_start',speed:50" in js.decode()
code="""import pathlib,hashlib,json,subprocess
root=pathlib.Path(%r)
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
protected={n:sha(root/n) for n in %r}
experiments={str(p):sha(p) for p in pathlib.Path('/home/sunrise/rbe201tc-sensor-studio/experiments').rglob('*') if p.is_file()}
print(json.dumps({'protected':protected,'experiments':experiments}))
"""%(pre['root'],list(pre['protected']))
r=subprocess.run(['ssh','-F','/Users/your-user/Library/Application Support/RBE201TC/connection/autoboat_ssh_config','-o','ClearAllForwardings=yes','-o','BatchMode=yes','-o','ConnectTimeout=4','rbe201tc-wifi','python3','-B','-'],input=code,text=True,capture_output=True,check=True)
data=json.loads(r.stdout);assert data['protected']==pre['protected'];assert data['experiments']==pre['experiments']
data.update(auto_max=50,gap_search=False,white_stops_immediately=True,command_mode='speed',motor_type=1,manual_unchanged=200,stopped=True,served_UI_verified=True,real_motion_sent=False)
(OUT/'final_verification.json').write_text(json.dumps(data,indent=2));print('Live prior output interface restored; cap50, stopped, protected motor/UI/data unchanged.')
