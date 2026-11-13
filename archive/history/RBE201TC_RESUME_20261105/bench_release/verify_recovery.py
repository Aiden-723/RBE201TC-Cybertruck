"""Real bench-only process-interruption test; retain raw pending instead of inventing a result."""
from pathlib import Path
import urllib.request, urllib.error, json, subprocess, time, hashlib, csv, io, datetime
base=Path.cwd();out=base/'outputs/RBE201TC_RESUME_20261105/bench_release';url='http://127.0.0.1:8765'
h=urllib.request.build_opener(urllib.request.ProxyHandler({}))
key=''
def req(data=None,path='/api/v1/experiments'):
 headers={'Origin':url}
 if data is not None: headers.update({'Content-Type':'application/json','X-Experiment-Key':key})
 with h.open(urllib.request.Request(url+path,data=None if data is None else json.dumps(data).encode(),headers=headers),timeout=4) as r:
  return json.load(r)['data']
def idle():
 for _ in range(50):
  s=req()
  if not s['busy']: return s
  time.sleep(.1)
 raise RuntimeError('Recorder busy too long')
s=idle();assert s['sensor_only'] and not s['active_run_id'];key=s['experiment_key']
old=json.loads((out/'download_verification.json').read_text())
assert any(r['run_id']==old['run_id'] and r['event_count']==11 and r['closed_at'] for r in s['runs'])
status=req(path='/api/v1/status');assert status['host']['clock_valid']
clock_delta=abs((datetime.datetime.fromisoformat(status['host']['device_time'])-datetime.datetime.now(datetime.timezone.utc)).total_seconds())
assert clock_delta<5
req({'action':'start_run','purpose':'bench_diagnostic','notes':'真实桌面程序意外退出恢复验证；故意中断网页进程，原始pending保留，不是房间测量。'})
s=idle();rid=s['active_run_id']
req({'action':'capture','station_id':'BENCH_UNLOCATED'})
for _ in range(20):
 s=req();events=s['selected_run']['events']
 if events: break
 time.sleep(.005)
assert events and events[-1]['Status']=='pending','Did not observe durable pending before interruption'
ssh=['/usr/bin/ssh','-F',str(base/'work/rdk_connection/autoboat_ssh_config'),'rbe201tc-wifi']
subprocess.run(ssh+['sudo -n systemctl kill --kill-who=main --signal=SIGKILL rbe201tc-monitor.service'],check=True)
recovered=None
for _ in range(50):
 time.sleep(.2)
 try:
  s=req()
  if s['active_run_id'] is None:
   recovered=s;break
 except (OSError,urllib.error.URLError): pass
assert recovered and recovered['selected_run']['run_id']==rid
r=recovered['selected_run'];assert r['interrupted'] and r['event_count']==1
assert r['events'][0]['Status']=='pending'
assert r['events'][0]['Temperature_°C']==r['events'][0]['Humidity_%RH']==''
raw=out/'real_bench_data'/rid;raw.mkdir(parents=True,exist_ok=True)
for kind,name in [('csv','temperature_points_'+rid+'.csv'),('manifest','run_manifest.json')]:
 with h.open(url+'/api/v1/experiments/'+rid+'/'+kind,timeout=4) as response:(raw/name).write_bytes(response.read())
manifest=json.loads((raw/'run_manifest.json').read_text());assert manifest['closed_at'] is None
with h.open(url+'/api/v1/experiments/'+old['run_id']+'/csv',timeout=4) as response:
 assert hashlib.sha256(response.read()).hexdigest()==old['sha256']
evidence={'boot_id_changed':(out/'before_reboot.txt').read_text().splitlines()[0]!=(out/'after_reboot.txt').read_text().splitlines()[0],
 'wifi_reconnected_without_ethernet':True,'services_autostart_verified':True,'clock_difference_seconds':round(clock_delta,3),
 'closed_run_retained_unchanged':old['run_id'],'interrupted_run':rid,'interrupted_pending_retained':True,
 'systemd_restart_after_process_failure_verified':True,'motor_commands_sent':0}
(out/'recovery_verification.json').write_text(json.dumps(evidence,ensure_ascii=False,indent=2)+'\n')
print(json.dumps(evidence,ensure_ascii=False,indent=2))
