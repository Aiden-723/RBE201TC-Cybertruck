"""Real bench-only acceptance; no motor commands, no generated sensor readings."""
from pathlib import Path
import urllib.request, urllib.error, json, time
BASE='http://127.0.0.1:8767'
out=Path(__file__).resolve().parent
http=urllib.request.build_opener(urllib.request.ProxyHandler({}))
def request(data=None):
    headers={'Origin':BASE}
    if data is not None: headers.update({'Content-Type':'application/json','X-Experiment-Key':key})
    req=urllib.request.Request(BASE+'/api/v1/experiments',data=None if data is None else json.dumps(data).encode(),headers=headers)
    with http.open(req,timeout=5) as r: return json.load(r)['data']
def idle():
    deadline=time.monotonic()+15
    while time.monotonic()<deadline:
        s=request()
        if not s['busy']:
            assert not s['error'],s['error']
            return s
        time.sleep(.15)
    raise RuntimeError('Recorder did not become idle')
s=idle();key=s['experiment_key'];run=s['active_run_id'];assert run and s['sensor_only']
assert s['selected_run']['purpose']=='bench_diagnostic'
for _ in range(9):
    request({'action':'capture','station_id':'BENCH_UNLOCATED'})
    s=idle();row=s['selected_run']['events'][-1]
    print(row['Detection point'],row['Status'],row['Temperature_°C'],row['Humidity_%RH'],flush=True)
request({'action':'capture','station_id':'BENCH_UNLOCATED'})
try:
    request({'action':'cancel'})
except urllib.error.HTTPError as error:
    assert error.code==409
s=idle()
proof={'run_id':run,'events':s['selected_run']['events'],'counts':s['selected_run']['status_counts'],'bench_only':True,'motor_commands_sent':0}
(out/'live_capture_before_close.json').write_text(json.dumps(proof,ensure_ascii=False,indent=2)+'\n')
print('Ready for UI close:',run,proof['counts'],flush=True)
