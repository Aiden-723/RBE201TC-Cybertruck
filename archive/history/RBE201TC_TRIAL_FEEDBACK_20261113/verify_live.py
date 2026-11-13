from pathlib import Path
import json,hashlib,subprocess,urllib.request
O=Path(__file__).resolve().parent;P=O.parents[1]/'work/device-console-v2';pre=json.loads((O/'production_before.json').read_text());payload=json.loads((O/'deployment_dry_check.json').read_text())
get=lambda n:json.load(urllib.request.urlopen('http://127.0.0.1:8765/api/v1/'+n,timeout=5))['data']
c=get('control');trials=get('line-trials');assert trials['enabled'] and trials['error'] is None
assert trials.get('annotation_key')
a=c['autonomy'];assert a['channel_permutation']==[3,2,1,0]
assert a['period_s']==.02 and a['max_correction_speed']==50
assert a['white_correction_hold_limit_s']==.2 and a['white_pivot_hold_limit_s']==.4
assert c['startup_boost_enabled'] and c['startup_boost_speed']==200 and c['startup_boost_duration_s']==.2
for name in ['index.html','trials.js','trials.css']:
 data=urllib.request.urlopen('http://127.0.0.1:8765/'+name,timeout=5).read()
 assert hashlib.sha256(data).hexdigest()==hashlib.sha256((P/'frontend'/name).read_bytes()).hexdigest(),name
names=list(payload['source_hashes'])+list(pre['protected'])
code="""import pathlib,json,hashlib,subprocess
r=pathlib.Path(%r);directory=pathlib.Path('/home/sunrise/rbe201tc-sensor-studio/line-trials');assert directory.is_dir()
print(json.dumps({'hashes':{n:hashlib.sha256((r/n).read_bytes()).hexdigest() for n in %r},'sensor_pid':int(subprocess.check_output(['systemctl','show','rbe201tc-sensors.service','-p','MainPID','--value'],text=True)),'monitor_pid':int(subprocess.check_output(['systemctl','show','rbe201tc-monitor.service','-p','MainPID','--value'],text=True)),'data_directory':str(directory),'data_files':[p.name for p in directory.iterdir() if p.is_file()],'experiments':{str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in pathlib.Path('/home/sunrise/rbe201tc-sensor-studio/experiments').rglob('*') if p.is_file()}}))
"""%(pre['root'],names)
r=subprocess.run(['ssh','-F','/Users/your-user/Library/Application Support/RBE201TC/connection/autoboat_ssh_config','-o','ClearAllForwardings=yes','-o','BatchMode=yes','-o','ConnectTimeout=4','rbe201tc-wifi','python3','-B','-'],input=code,text=True,capture_output=True,check=True)
d=json.loads(r.stdout)
for n,h in {**pre['protected'],**payload['source_hashes']}.items():assert d['hashes'][n]==h,n
assert d['sensor_pid']==pre['sensor_service_pid'] and d['experiments']==pre['experiments']
d.update(recorder_enabled=True,recorder_error=trials['error'],active_trial=trials['active_id'],record_count=len(trials['records']),current_control={k:c.get(k) for k in ['armed','state','outputs','drive_commands_sent']},served_assets_verified=True,motion_sent_by_assistant=False,live_physical_trial_tested=False)
(O/'final_verification.json').write_text(json.dumps(d,ensure_ascii=False,indent=2)+'\n')
print(json.dumps({k:v for k,v in d.items() if k not in ('hashes','experiments')},ensure_ascii=False))
