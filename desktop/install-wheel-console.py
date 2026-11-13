"""Run on RDK after staging and fake/virtual serial tests. Never commands motion."""
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import tarfile
import time
import urllib.request

root = Path('/home/sunrise/rbe201tc-sensor-studio')
live = root / 'device-console-v2'
stage = root / 'wheel-stage/device-console-v2'
files = ['backend/server.py', 'backend/motor_control.py', 'backend/README.md',
         'backend/tests/test_motor_control.py', 'frontend/index.html', 'frontend/styles.css',
         'frontend/app.js', 'frontend/control.js', 'frontend/tests/control.test.cjs', 'README.md']
for name in files:
    if not (stage / name).is_file():
        raise RuntimeError('missing staged file: '+name)
web_pid = int((root/'web.pid').read_text().strip())
cmd = Path(f'/proc/{web_pid}/cmdline').read_bytes().split(b'\0')
expected = str(live/'backend/server.py').encode()
if expected not in cmd:
    raise RuntimeError('web PID does not match known console')
busy = subprocess.run(['fuser', '/dev/ttyUSB0'], capture_output=True, text=True)
if busy.returncode not in (0, 1) or (busy.returncode == 0 and set(busy.stdout.split()) != {str(web_pid)}):
    raise RuntimeError('motor serial port has another owner: '+busy.stdout)
backups = root/'backups'
backups.mkdir(exist_ok=True)
backup = backups/'console-before-speed-fix-20261103.tgz'
if backup.exists():
    raise RuntimeError('backup already exists; do not overwrite rollback')
with tarfile.open(backup, 'w:gz') as archive:
    for name in files:
        if (live/name).is_file():
            archive.add(live/name, arcname=name)
os.kill(web_pid, signal.SIGTERM)
for _ in range(50):
    try:
        os.kill(web_pid, 0)
    except ProcessLookupError:
        break
    stat = Path(f'/proc/{web_pid}/stat')
    if stat.exists() and stat.read_text().split()[2] == 'Z':
        break
    time.sleep(.1)
else:
    raise RuntimeError('old web server did not exit')
for name in files:
    destination = live/name
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(destination.name+'.wheel-new')
    shutil.copy2(stage/name, temporary)
    os.replace(temporary, destination)
with (root/'web.log').open('ab', buffering=0) as log:
    process = subprocess.Popen(['/usr/bin/python3', str(live/'backend/server.py'), '--port','8765',
        '--sensor-state-dir','/dev/shm/rbe201tc-sensor-studio', '--motor-port','/dev/ttyUSB0'],
        cwd=live, stdin=subprocess.DEVNULL, stdout=log, stderr=log, start_new_session=True)
(root/'web.pid').write_text(str(process.pid)+'\n')
for _ in range(50):
    if process.poll() is not None:
        raise RuntimeError('new server exited; inspect web.log; rollback: '+str(backup))
    try:
        with urllib.request.urlopen('http://127.0.0.1:8765/api/v1/control', timeout=1) as response:
            state=json.load(response)['data']
        if state['enabled'] and not state['armed'] and state['outputs']==[0,0,0,0] and not state.get('fault'):
            print(json.dumps({'web_pid':process.pid,'backup':str(backup),'control_state':state['state'],
                              'outputs':state['outputs'],'mode':state['command_mode'],'nonzero_commands_sent':False}))
            break
    except OSError:
        pass
    time.sleep(.1)
else:
    raise RuntimeError('new server not ready; rollback: '+str(backup))
