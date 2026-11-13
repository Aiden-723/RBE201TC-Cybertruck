"""One-time guarded migration from the observed manual processes to sensor-only units."""
from pathlib import Path
import json, os, signal, select, subprocess, time, urllib.request, shutil
base=Path('/home/sunrise/rbe201tc-sensor-studio')
old=base/'device-console-v2'; current=base/'readonly-current'
backup=base/'recovery-20261105-services';backup.mkdir(exist_ok=False)
units=('rbe201tc-sensors.service','rbe201tc-monitor.service')
webargs=['/usr/bin/python3','-u','backend/server.py','--host','127.0.0.1','--port','8765','--sensor-state-dir','/dev/shm/rbe201tc-sensor-studio']
sensorargs=['/usr/bin/python3',str(old/'backend/hardware_worker.py'),'--state-dir','/dev/shm/rbe201tc-sensor-studio','--dht-binary',str(old/'backend/native/dht11_server')]
webpid=int((base/'web.pid').read_text());sensorpid=int(Path('/dev/shm/rbe201tc-sensor-studio/supervisor.pid').read_text())
def identity(pid,args):
    fd=os.pidfd_open(pid)
    actual=Path(f'/proc/{pid}/cmdline').read_bytes().rstrip(b'\0').decode().split('\0')
    if actual!=args:
        os.close(fd);raise RuntimeError(f'PID {pid} identity changed')
    return fd
webfd=identity(webpid,webargs);sensorfd=identity(sensorpid,sensorargs)
assert Path(f'/proc/{webpid}/cwd').resolve()==old
with urllib.request.urlopen('http://127.0.0.1:8767/api/v1/experiments',timeout=3) as r:
    state=json.load(r)['data'];assert state['active_run_id'] is None and not state['busy']
for name in units:
    assert not (Path('/etc/systemd/system')/name).exists()
(backup/'before.json').write_text(json.dumps({'webpid':webpid,'sensorpid':sensorpid,'webargs':webargs,'sensorargs':sensorargs,'release':str(current.resolve())},indent=2))
subprocess.run(['systemctl','stop','rbe201tc-bench-stage.service'],check=True)
stopped=[]
def stop(fd,label):
    signal.pidfd_send_signal(fd,signal.SIGTERM)
    ready=select.select([fd],[],[],15)[0]
    if not ready: raise RuntimeError(label+' did not terminate')
    stopped.append(label)
try:
    stop(webfd,'web');stop(sensorfd,'sensors')
    leftovers=[]
    for p in Path('/proc').iterdir():
        if p.name.isdigit():
            try:
                parts=(p/'cmdline').read_bytes().split(b'\0')
                if any(x.endswith(b'/hardware_worker.py') or x.endswith(b'/dht11_server') for x in parts):
                    leftovers.append(p.name)
            except (FileNotFoundError,ProcessLookupError): pass
    assert not leftovers,('Existing hardware owners',leftovers)
    for name in units:
        target=Path('/etc/systemd/system')/name
        shutil.copyfile(current/'deploy'/name,target);target.chmod(0o644)
    subprocess.run(['systemctl','daemon-reload'],check=True)
    subprocess.run(['systemctl','enable','--now',*units],check=True)
    for _ in range(30):
        try:
            with urllib.request.urlopen('http://127.0.0.1:8765/api/v1/experiments',timeout=2) as r:
                state=json.load(r)['data']
            assert state['sensor_only'] and state['active_run_id'] is None
            assert any(x['run_id']=='20261105T140833911790Z_34d0bc26' for x in state['runs'])
            break
        except (OSError,AssertionError): time.sleep(.5)
    else: raise RuntimeError('New monitor failed readiness/history check')
    pid=subprocess.check_output(['systemctl','show','rbe201tc-monitor','-p','MainPID','--value'],text=True).strip()
    (base/'web.pid').write_text(pid+'\n');os.chown(base/'web.pid',1000,1000)
    (backup/'result.json').write_text(json.dumps({'status':'success','web_pid':int(pid),'sensor_only':True,'history_recovered':True},indent=2))
    print((backup/'result.json').read_text())
except Exception:
    subprocess.run(['systemctl','disable','--now',*units])
    for label,args,user in [('sensors',sensorargs,None),('web',webargs,'sunrise')]:
        if label in stopped:
            command=args if user is None else ['runuser','-u',user,'--',*args]
            with (backup/(label+'-rollback.log')).open('ab') as log:
                p=subprocess.Popen(command,cwd=old,stdout=log,stderr=log,start_new_session=True)
            if label=='web': (base/'web.pid').write_text(str(p.pid)+'\n')
    raise
finally:
    os.close(webfd);os.close(sensorfd)
