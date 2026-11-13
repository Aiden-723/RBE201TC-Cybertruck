from pathlib import Path
import hashlib,json,subprocess,urllib.request
O=Path(__file__).resolve().parent;P=O.parents[1]/'work/device-console-v2'
pre=json.loads((O/'production_before.json').read_text());payload=json.loads((O/'deployment_dry_check.json').read_text())
get=lambda n:json.load(urllib.request.urlopen('http://127.0.0.1:8765/api/v1/'+n,timeout=5))['data']
c=get('control');trials=get('line-trials');s=get('status');a=c['autonomy']
assert a['control_policy']=='four_digital_continuous_turn_v1' and a['center_confirmation_samples']==2
assert a['channel_permutation']==[3,2,1,0] and a['period_s']==.02 and a['max_correction_speed']==40
assert a['white_correction_hold_limit_s']==.2 and a['white_pivot_hold_limit_s']==.4
assert a['speed'] in (10,20,40)
assert c['startup_boost_enabled'] and c['startup_boost_speed']==200 and c['startup_boost_duration_s']==.2
assert c['command_mode']=='speed' and trials['enabled'] and trials['error'] is None
assert hashlib.sha256(urllib.request.urlopen('http://127.0.0.1:8765/control.js',timeout=5).read()).hexdigest()==hashlib.sha256((P/'frontend/control.js').read_bytes()).hexdigest()
for asset in ['index.html','cockpit.css']:
 assert hashlib.sha256(urllib.request.urlopen('http://127.0.0.1:8765/'+asset,timeout=5).read()).hexdigest()==hashlib.sha256((P/'frontend'/asset).read_bytes()).hexdigest()
names=list(payload['source_hashes'])+list(pre['protected'])
code="""import pathlib,json,hashlib,subprocess
r=pathlib.Path(%r);t=pathlib.Path('/home/sunrise/rbe201tc-sensor-studio/line-trials');sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
print(json.dumps({'hashes':{n:sha(r/n) for n in %r},'sensor_pid':int(subprocess.check_output(['systemctl','show','rbe201tc-sensors.service','-p','MainPID','--value'],text=True)),'monitor_pid':int(subprocess.check_output(['systemctl','show','rbe201tc-monitor.service','-p','MainPID','--value'],text=True)),'trial_files':{str(p.relative_to(t)):sha(p) for p in t.rglob('*') if p.is_file()},'experiments':{str(p):sha(p) for p in pathlib.Path('/home/sunrise/rbe201tc-sensor-studio/experiments').rglob('*') if p.is_file()}}))
"""%(pre['root'],names)
r=subprocess.run(['ssh','-F','/Users/your-user/Library/Application Support/RBE201TC/connection/autoboat_ssh_config','-o','ClearAllForwardings=yes','-o','BatchMode=yes','-o','ConnectTimeout=4','rbe201tc-wifi','python3','-B','-'],input=code,text=True,capture_output=True,timeout=20,check=True)
d=json.loads(r.stdout)
for n,h in {**pre['protected'],**payload['source_hashes']}.items():assert d['hashes'][n]==h,n
assert d['sensor_pid']==pre['sensor_service_pid'] and d['experiments']==pre['experiments']
prior=json.loads((O/'trial_files_before.json').read_text());assert all(d['trial_files'].get(n)==h for n,h in prior.items())
d.update(previous_trial_files_unchanged=len(prior),record_count=len(trials['records']),recorder_error=trials['error'],active_trial=trials['active_id'],
         current_control={k:c.get(k) for k in ['armed','state','outputs','drive_commands_sent']},
         sensor_status={k:s['sensors'][k]['status'] for k in ['line_sensors','camera','dht11']},served_control_js_verified=True,
         motion_sent_by_assistant=False,physical_turn_passed=False)
(O/'final_verification.json').write_text(json.dumps(d,ensure_ascii=False,indent=2)+'\n')
print(json.dumps({k:v for k,v in d.items() if k not in ['hashes','experiments','trial_files']},ensure_ascii=False,indent=2))
