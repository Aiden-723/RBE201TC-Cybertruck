from pathlib import Path
import json,hashlib,subprocess,urllib.request
O=Path(__file__).resolve().parent;P=O.parents[1]/'work/device-console-v2';pre=json.loads((O/'production_before.json').read_text());payload=json.loads((O/'deployment_dry_check.json').read_text())
get=lambda n:json.load(urllib.request.urlopen('http://127.0.0.1:8765/api/v1/'+n,timeout=5))['data']
c=get('control');a=c['autonomy'];s=get('status')
assert a['channel_permutation']==[0,1,2,3] and a['channel_order']==['X1','X2','X3','X4']
assert a['raw_channel_order']==['X1','X2','X3','X4']
assert c['startup_boost_enabled'] and c['startup_boost_speed']==200 and c['startup_boost_duration_s']==.2
assert a['max_correction_speed']==50 and a['white_correction_hold_limit_s']==.2 and a['white_pivot_hold_limit_s']==.4
assert c['command_mode']=='speed' and 'Motor_type:1' in c['serial_diagnostics']['configuration']
js=urllib.request.urlopen('http://127.0.0.1:8765/control.js',timeout=5).read()
assert hashlib.sha256(js).hexdigest()==hashlib.sha256((P/'frontend/control.js').read_bytes()).hexdigest()
assert '车身左→右为X1→X4' in js.decode() and '车身左→右为X4→X1' not in js.decode()
assert '见线按官方组合规则纠偏' in js.decode()
assert '短暂丢线 · 延续上一动作' in js.decode()
assert '短丢线直行及轻纠偏0.2秒／强纠偏0.4秒' in js.decode()
names=list(payload['source_hashes'])+list(pre['protected'])
code="import pathlib,json,hashlib,subprocess\nr=pathlib.Path(%r)\nprint(json.dumps({'hashes':{n:hashlib.sha256((r/n).read_bytes()).hexdigest() for n in %r},'sensor_pid':int(subprocess.check_output(['systemctl','show','rbe201tc-sensors.service','-p','MainPID','--value'],text=True)),'monitor_pid':int(subprocess.check_output(['systemctl','show','rbe201tc-monitor.service','-p','MainPID','--value'],text=True))}))"%(pre['root'],names)
r=subprocess.run(['ssh','-F','/Users/your-user/Library/Application Support/RBE201TC/connection/autoboat_ssh_config','-o','ClearAllForwardings=yes','-o','BatchMode=yes','-o','ConnectTimeout=4','rbe201tc-wifi','python3','-B','-'],input=code,text=True,capture_output=True,check=True)
d=json.loads(r.stdout)
for n,h in {**pre['protected'],**payload['source_hashes']}.items():assert d['hashes'][n]==h,n
assert d['sensor_pid']==pre['sensor_service_pid']
assert s['sensors']['line_sensors']['status']=='live'
d.update(channel_order=a['channel_order'],permutation=a['channel_permutation'],current_control={k:c.get(k) for k in ['armed','state','reason','outputs','requested_outputs','drive_commands_sent']},sensor_status={k:v.get('status') for k,v in s['sensors'].items()},served_ui_verified=True,motion_sent_by_assistant=False,physical_driving_verified=False)
(O/'final_verification.json').write_text(json.dumps(d,ensure_ascii=False,indent=2)+'\n')
print(json.dumps({k:v for k,v in d.items() if k!='hashes'},ensure_ascii=False))
