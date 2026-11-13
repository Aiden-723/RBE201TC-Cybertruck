from pathlib import Path
import json,hashlib,shutil,subprocess
OUT=Path(__file__).resolve().parent;ROOT=OUT.parents[1];P=ROOT/'work/device-console-v2'
sha=lambda b:hashlib.sha256(b).hexdigest()
v6=json.loads((ROOT/'outputs/RBE201TC_RIGHT_ANGLE_20261111/integration/round7/deployment/robot_deployment_result.json').read_text())['hashes']
sources={n:ROOT/'outputs/RBE201TC_V7_20261112/before'/n for n in v6}
sources.update({'backend/motor_control.py':ROOT/'outputs/RBE201TC_V7_1_20261112/before/backend/motor_control.py','backend/tests/test_autonomous_motor_bridge.py':ROOT/'outputs/RBE201TC_V7_1_20261112/before/backend/tests/test_autonomous_motor_bridge.py','backend/driving_mode.py':ROOT/'outputs/RBE201TC_SOURCE_ADAPTER_20261112/before/backend/driving_mode.py'})
protected=json.loads((ROOT/'outputs/RBE201TC_V7_20261112/production_before.json').read_text())['protected']
for n,s in sources.items():
 if n in v6:assert sha(s.read_bytes())==v6[n]
 if n in protected:assert sha(s.read_bytes())==protected[n]
deleted=['backend/source_line_follower.py','backend/tests/test_source_line_follower.py','backend/tests/fixtures/yahboom_truth.json','backend/tests/test_line_v7.py']
protected={n:h for n,h in protected.items() if n not in sources}
files=list(sources);all_names=files+deleted
code='''import pathlib,json,hashlib,subprocess,urllib.request
root=pathlib.Path('/home/sunrise/rbe201tc-sensor-studio/releases/cockpit_driving_20261106')
get=lambda n:json.load(urllib.request.urlopen('http://127.0.0.1:8765/api/v1/'+n,timeout=4))['data']
c=get('control');a=get('experiments')
assert not c['armed'] and c['outputs']==[0]*4 and not c['fault'] and not c['autonomy']['running']
assert not a.get('active_run_id') and not any(a.get(k) for k in ('busy','committing','closing'))
pid=int(subprocess.check_output(['systemctl','show','rbe201tc-monitor.service','-p','MainPID','--value'],text=True))
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
print(json.dumps({'root':str(root),'pid':pid,'argv':pathlib.Path('/proc/%%d/cmdline'%%pid).read_bytes().decode().strip('\\0').split('\\0'),'sensor_service_pid':int(subprocess.check_output(['systemctl','show','rbe201tc-sensors.service','-p','MainPID','--value'],text=True)),'before':{n:{'sha256':sha(root/n)} for n in %r},'protected':{n:sha(root/n) for n in %r},'experiments':{str(p):sha(p) for p in pathlib.Path('/home/sunrise/rbe201tc-sensor-studio/experiments').rglob('*') if p.is_file()},'last_control':{k:v for k,v in c.items() if k not in ['control_key','session','autonomy_session']}}))
'''%(all_names,list(protected))
r=subprocess.run(['ssh','-F','/Users/your-user/Library/Application Support/RBE201TC/connection/autoboat_ssh_config','-o','ClearAllForwardings=yes','-o','ConnectTimeout=4','rbe201tc-wifi','python3','-B','-'],input=code,text=True,capture_output=True,check=True);pre=json.loads(r.stdout)
for n,info in pre['before'].items():assert sha((P/n).read_bytes())==info['sha256'],('Local/live differ',n)
for n,h in pre['protected'].items():assert sha((P/n).read_bytes())==h==protected[n]
(OUT/'production_before.json').write_text(json.dumps(pre,ensure_ascii=False,indent=2))
for n in all_names:
 dst=OUT/'before'/n;dst.parent.mkdir(parents=True,exist_ok=True);assert not dst.exists();shutil.copy2(P/n,dst)
for n,src in sources.items():shutil.copy2(src,P/n)
for n in deleted:(P/n).unlink() # All bytes archived above; only future-version code/tests.
(OUT/'rollback_manifest.json').write_text(json.dumps({'files':files,'deleted':{n:pre['before'][n]['sha256'] for n in deleted},'v6_hashes':{n:sha((P/n).read_bytes()) for n in files}},indent=2))
print('Exact v6 bytes restored locally; newer code archived.')
